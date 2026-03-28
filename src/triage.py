"""Session Item Triage — scores, routes, and prioritizes digest items.

Ported from triage-app's scoring + routing logic, adapted for voice session
notes instead of Jira tickets. Each item gets:

1. **Scored** — weighted checks for actionability, specificity, context
2. **Routed** — keyword matching assigns a project theme
3. **Graded** — actionable / needs-context / parked / stale
4. **Tracked** — analytics on what themes come up, what gets parked

The triage can be trained with project-specific keywords via train().
"""

import json
import os
import re
from datetime import datetime

# ---------------------------------------------------------------------------
# Keyword → Theme routing (equivalent to triage-app's TEAM_KEYWORDS)
# ---------------------------------------------------------------------------

# Default routing table — project-specific, trainable
DEFAULT_THEME_KEYWORDS = {
    "Game Systems": [
        "sensor", "signal", "gate", "logic", "network", "path", "pathfind",
        "maze", "node", "vine", "turret", "tower", "wave", "spawn",
        "cooldown", "range", "detection", "trigger", "chain", "amplify",
        "combat", "damage", "health", "targeting", "projectile", "aoe",
        "draft", "build phase", "placement", "upgrade",
    ],
    "Content": [
        "map", "layout", "level", "enemy", "boss", "tier", "wave design",
        "arena", "sector", "room", "item", "loot", "drop", "reward",
        "progression", "unlock", "new enemy", "new map", "new wave",
        "scrap", "currency", "economy",
    ],
    "Visual Polish": [
        "ui", "ux", "screen", "menu", "hud", "button", "panel",
        "vfx", "particle", "shader", "material", "mesh", "model",
        "animation", "anim", "sprite", "icon", "color", "theme",
        "glow", "pulse", "fade", "transition", "tron", "grid line",
        "emissive", "cyberpunk",
    ],
    "Balance / Tuning": [
        "balance", "tuning", "number", "stat", "dps", "difficulty",
        "scaling", "curve", "pacing", "speed", "rate", "cost",
        "hp", "damage number", "multiplier", "percent", "ratio",
        "too easy", "too hard", "overpowered", "underpowered", "nerf", "buff",
    ],
    "Architecture / Code": [
        "refactor", "pattern", "singleton", "service", "event bus",
        "registry", "manager", "component", "scene", "node tree",
        "autoload", "signal bus", "c#", "godot", "performance",
        "memory", "fps", "gc", "allocation", "bug", "crash",
        "debug", "editor", "tool", "pipeline", "build",
    ],
    "AXIS / Lore": [
        "axis", "bit", "lore", "story", "dialogue", "commentary",
        "voice line", "personality", "snark", "taunt", "junkyard",
        "robot", "scrapper", "arena", "narrator", "flavor text",
    ],
}

DEFAULT_THEME = "Game Systems"

# ---------------------------------------------------------------------------
# Scoring weights (equivalent to triage-app's WEIGHTS)
# ---------------------------------------------------------------------------

WEIGHTS = {
    "specificity": 20,    # Does the item name concrete things? (not vague)
    "actionability": 20,  # Can someone act on this right now?
    "context": 20,        # Does it have enough detail to understand?
    "tag_match": 15,      # Does the tag match what the item actually is?
    "theme_signal": 15,   # Does it clearly belong to a theme?
    "feedback_signal": 10, # User feedback: follows boost, dismissals suppress
}

# ---------------------------------------------------------------------------
# Scoring checks (equivalent to triage-app's check* functions)
# ---------------------------------------------------------------------------

def check_specificity(item: dict) -> dict:
    """Items that name concrete things score higher than vague ones."""
    text = item["text"].lower()

    # Vague indicators
    vague_phrases = [
        "something", "maybe", "somehow", "stuff", "thing", "whatever",
        "we should probably", "might want to", "could be",
    ]
    vague_count = sum(1 for p in vague_phrases if p in text)

    # Specific indicators: numbers, named systems, file references
    has_numbers = bool(re.search(r'\d+', text))
    has_named_entity = bool(re.search(
        r'\b(?:wave|sensor|gate|turret|enemy|boss|map|screen|node|signal)\b', text
    ))
    word_count = len(text.split())

    score = WEIGHTS["specificity"]
    if vague_count >= 2:
        score = int(score * 0.2)
    elif vague_count == 1:
        score = int(score * 0.5)

    if not has_numbers and not has_named_entity:
        score = int(score * 0.6)

    if word_count < 4:
        score = int(score * 0.5)

    return {
        "check": "specificity",
        "score": score,
        "max_score": WEIGHTS["specificity"],
        "detail": f"{word_count} words, {vague_count} vague phrases, "
                  f"{'has' if has_numbers else 'no'} numbers, "
                  f"{'has' if has_named_entity else 'no'} named entities",
    }


def check_actionability(item: dict) -> dict:
    """ACTIONs and DECISIONs are inherently more actionable."""
    tag = item["tag"]
    text = item["text"].lower()

    # Tag-based baseline
    tag_scores = {
        "ACTION": 1.0,
        "DECISION": 0.9,
        "IDEA": 0.5,
        "WATCH": 0.4,
        "QUESTION": 0.3,
    }
    base = tag_scores.get(tag, 0.5)

    # Boost for imperative language
    imperative = ["need to", "must", "should", "will", "add", "fix", "remove",
                  "implement", "wire up", "create", "build", "update"]
    has_imperative = any(p in text for p in imperative)
    if has_imperative:
        base = min(1.0, base + 0.2)

    # Penalty for hedging
    hedging = ["might", "perhaps", "someday", "eventually", "low priority"]
    if any(h in text for h in hedging):
        base = max(0.1, base - 0.2)

    score = int(WEIGHTS["actionability"] * base)

    return {
        "check": "actionability",
        "score": score,
        "max_score": WEIGHTS["actionability"],
        "detail": f"tag={tag}, imperative={'yes' if has_imperative else 'no'}",
    }


def check_context(item: dict) -> dict:
    """Does the item have enough words/detail to be understood standalone?"""
    text = item["text"]
    word_count = len(text.split())

    if word_count >= 10:
        score = WEIGHTS["context"]
    elif word_count >= 6:
        score = int(WEIGHTS["context"] * 0.7)
    elif word_count >= 3:
        score = int(WEIGHTS["context"] * 0.4)
    else:
        score = int(WEIGHTS["context"] * 0.1)

    return {
        "check": "context",
        "score": score,
        "max_score": WEIGHTS["context"],
        "detail": f"{word_count} words",
    }


def check_tag_match(item: dict) -> dict:
    """Heuristic: does the text match what the tag claims?"""
    tag = item["tag"]
    text = item["text"].lower()

    score = WEIGHTS["tag_match"]  # assume match unless flagged

    if tag == "DECISION" and "?" in text:
        score = int(score * 0.3)  # decisions shouldn't be questions
    elif tag == "QUESTION" and "?" not in text:
        score = int(score * 0.5)  # questions should have question marks
    elif tag == "ACTION" and not any(w in text for w in [
        "need", "add", "fix", "create", "build", "wire", "implement",
        "update", "remove", "set up", "write", "test", "deploy",
    ]):
        score = int(score * 0.6)  # actions should have action verbs

    return {
        "check": "tag_match",
        "score": score,
        "max_score": WEIGHTS["tag_match"],
        "detail": f"tag={tag}",
    }


def check_theme_signal(item: dict, theme_keywords: dict = None) -> dict:
    """Does the item clearly belong to a theme via keyword matching?"""
    if theme_keywords is None:
        theme_keywords = DEFAULT_THEME_KEYWORDS

    text = item["text"].lower()
    best_theme = None
    best_score = 0

    for theme, keywords in theme_keywords.items():
        hits = 0
        for kw in keywords:
            if len(kw) <= 3:
                if re.search(rf'\b{re.escape(kw)}\b', text):
                    hits += 1
            else:
                if kw in text:
                    hits += 1
        if hits > best_score:
            best_score = hits
            best_theme = theme

    if best_score >= 2:
        score = WEIGHTS["theme_signal"]
    elif best_score == 1:
        score = int(WEIGHTS["theme_signal"] * 0.6)
    else:
        score = 0

    return {
        "check": "theme_signal",
        "score": score,
        "max_score": WEIGHTS["theme_signal"],
        "inferred_theme": best_theme,
        "keyword_hits": best_score,
        "detail": f"best theme: {best_theme} ({best_score} hits)",
    }


def check_feedback_signal(item: dict, effective_weights: dict = None) -> dict:
    """Score adjustment based on accumulated user feedback on similar terms.

    Terms from followed/backlogged items boost the score.
    Terms from dismissed items suppress it.
    No feedback data = neutral (5/10).
    """
    if not effective_weights:
        return {
            "check": "feedback_signal",
            "score": WEIGHTS["feedback_signal"] // 2,  # neutral
            "max_score": WEIGHTS["feedback_signal"],
            "detail": "no feedback data",
        }

    from user_db import extract_terms
    terms = extract_terms(item["text"])

    if not terms:
        return {
            "check": "feedback_signal",
            "score": WEIGHTS["feedback_signal"] // 2,
            "max_score": WEIGHTS["feedback_signal"],
            "detail": "no extractable terms",
        }

    # Sum weights for matching terms
    total_weight = 0
    matched = 0
    for term in terms:
        if term in effective_weights:
            total_weight += effective_weights[term]["weight"]
            matched += 1

    if matched == 0:
        score = WEIGHTS["feedback_signal"] // 2  # neutral
    else:
        # Normalize: avg weight per matched term, map from [-100,+100] to [0, max]
        avg_weight = total_weight / matched
        # Map -100..+100 to 0..max_score (midpoint = half)
        max_score = WEIGHTS["feedback_signal"]
        score = int(max_score * (avg_weight + 100) / 200)
        score = max(0, min(max_score, score))

    return {
        "check": "feedback_signal",
        "score": score,
        "max_score": WEIGHTS["feedback_signal"],
        "detail": f"{matched} term matches, net weight {total_weight:+.0f}",
    }


# ---------------------------------------------------------------------------
# Route: keyword → theme (equivalent to triage-app's inferTeam)
# ---------------------------------------------------------------------------

def route_to_theme(text: str, theme_keywords: dict = None,
                   feedback_weights: dict = None) -> str:
    """Assign an item to a project theme via keyword matching.

    If feedback_weights contains terms with explicit theme assignments,
    those can override keyword routing when they match strongly.
    """
    if theme_keywords is None:
        theme_keywords = DEFAULT_THEME_KEYWORDS

    lower = text.lower()
    best_theme = DEFAULT_THEME
    best_hits = 0

    for theme, keywords in theme_keywords.items():
        hits = 0
        for kw in keywords:
            if len(kw) <= 3:
                if re.search(rf'\b{re.escape(kw)}\b', lower):
                    hits += 1
            else:
                if kw in lower:
                    hits += 1
        if hits > best_hits:
            best_hits = hits
            best_theme = theme

    # Check for explicit theme overrides from user term DB
    if feedback_weights:
        from user_db import extract_terms
        terms = extract_terms(text)
        theme_votes = {}
        for term in terms:
            entry = feedback_weights.get(term)
            if entry and entry.get("theme") and entry.get("source") == "explicit":
                t = entry["theme"]
                theme_votes[t] = theme_votes.get(t, 0) + 1
        # Explicit theme override wins if 2+ terms agree or keyword match was weak
        if theme_votes:
            top_theme = max(theme_votes, key=theme_votes.get)
            if theme_votes[top_theme] >= 2 or best_hits <= 1:
                best_theme = top_theme

    return best_theme


# ---------------------------------------------------------------------------
# Score an item (equivalent to triage-app's scoreBug)
# ---------------------------------------------------------------------------

def score_item(item: dict, theme_keywords: dict = None,
               feedback_weights: dict = None) -> dict:
    """Score a single digest item. Returns full triage result.

    Args:
        feedback_weights: dict from UserDB.get_effective_weights() —
            {term: {weight, theme, source}}. If provided, adds feedback_signal check.
    """
    checks = [
        check_specificity(item),
        check_actionability(item),
        check_context(item),
        check_tag_match(item),
        check_theme_signal(item, theme_keywords),
        check_feedback_signal(item, feedback_weights),
    ]

    total = sum(c["score"] for c in checks)
    max_possible = sum(c["max_score"] for c in checks)

    # Grade based on score
    if total >= 80:
        grade = "actionable"
    elif total >= 55:
        grade = "needs-context"
    elif total >= 30:
        grade = "parked"
    else:
        grade = "stale"

    # Inferred theme from the theme_signal check
    theme_check = next(c for c in checks if c["check"] == "theme_signal")
    inferred_theme = theme_check.get("inferred_theme", DEFAULT_THEME)

    # Override: route via full keyword matching (+ term DB theme overrides)
    routed_theme = route_to_theme(item["text"], theme_keywords, feedback_weights)

    return {
        "tag": item["tag"],
        "text": item["text"],
        "time": item.get("time", ""),
        "score": total,
        "max_possible": max_possible,
        "grade": grade,
        "theme": routed_theme or inferred_theme or DEFAULT_THEME,
        "checks": checks,
    }


# ---------------------------------------------------------------------------
# Triage a full session's items
# ---------------------------------------------------------------------------

def triage_session(items: list[dict], theme_keywords: dict = None,
                   feedback_weights: dict = None) -> dict:
    """Score and route all items from a session.

    Args:
        feedback_weights: dict from UserDB.get_effective_weights() for
            feedback-driven scoring adjustments.

    Returns:
        results: list of scored items
        summary: aggregate stats
    """
    results = []
    for item in items:
        results.append(score_item(item, theme_keywords, feedback_weights))

    # Build summary
    by_grade = {}
    by_theme = {}
    by_tag = {}
    for r in results:
        by_grade[r["grade"]] = by_grade.get(r["grade"], 0) + 1
        by_theme[r["theme"]] = by_theme.get(r["theme"], 0) + 1
        by_tag[r["tag"]] = by_tag.get(r["tag"], 0) + 1

    return {
        "results": results,
        "summary": {
            "total": len(results),
            "by_grade": by_grade,
            "by_theme": by_theme,
            "by_tag": by_tag,
            "avg_score": sum(r["score"] for r in results) / len(results) if results else 0,
        },
    }


# ---------------------------------------------------------------------------
# Training: customize keywords for your project
# ---------------------------------------------------------------------------

TRAINING_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "triage_training.json")


def load_training(path: str = TRAINING_PATH) -> dict:
    """Load custom theme keywords from training file."""
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Merge with defaults — training adds/overrides
        merged = dict(DEFAULT_THEME_KEYWORDS)
        for theme, keywords in data.get("theme_keywords", {}).items():
            if theme in merged:
                # Add new keywords, deduplicate
                existing = set(merged[theme])
                merged[theme] = list(existing | set(keywords))
            else:
                merged[theme] = keywords
        return merged
    return dict(DEFAULT_THEME_KEYWORDS)


def save_training(theme_keywords: dict, path: str = TRAINING_PATH):
    """Save custom theme keywords to training file."""
    # Only save keywords that differ from defaults
    custom = {}
    for theme, keywords in theme_keywords.items():
        default_set = set(DEFAULT_THEME_KEYWORDS.get(theme, []))
        custom_kws = [kw for kw in keywords if kw not in default_set]
        if custom_kws:
            custom[theme] = custom_kws

    with open(path, "w", encoding="utf-8") as f:
        json.dump({"theme_keywords": custom, "updated": datetime.now().isoformat()}, f, indent=2)


def train(theme: str, keywords: list[str], path: str = TRAINING_PATH):
    """Add keywords to a theme's routing table."""
    current = load_training(path)
    if theme not in current:
        current[theme] = []
    existing = set(current[theme])
    current[theme] = list(existing | set(keywords))
    save_training(current, path)
    return current


# ---------------------------------------------------------------------------
# Analytics (equivalent to triage-app's AnalyticsStore)
# ---------------------------------------------------------------------------

ANALYTICS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "triage_analytics.json")


class TriageAnalytics:
    """Tracks triage metrics across sessions. JSON file on disk."""

    def __init__(self, path: str = ANALYTICS_PATH):
        self.path = path
        self.data = self._load()

    def _load(self) -> dict:
        defaults = {
            "total_processed": 0,
            "by_grade": {},
            "by_theme": {},
            "by_tag": {},
            "sessions": [],
            "score_history": [],  # last 100 avg scores
        }
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    stored = json.load(f)
                return {**defaults, **stored}
            except (json.JSONDecodeError, OSError):
                pass
        return defaults

    def _save(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2)

    def record_session(self, summary: dict):
        """Record a triage session's summary."""
        self.data["total_processed"] += summary["total"]

        for grade, count in summary["by_grade"].items():
            self.data["by_grade"][grade] = self.data["by_grade"].get(grade, 0) + count
        for theme, count in summary["by_theme"].items():
            self.data["by_theme"][theme] = self.data["by_theme"].get(theme, 0) + count
        for tag, count in summary["by_tag"].items():
            self.data["by_tag"][tag] = self.data["by_tag"].get(tag, 0) + count

        self.data["sessions"].append({
            "date": datetime.now().isoformat(),
            "items": summary["total"],
            "avg_score": round(summary["avg_score"], 1),
        })
        # Keep last 50 sessions
        self.data["sessions"] = self.data["sessions"][-50:]

        self.data["score_history"].append(round(summary["avg_score"], 1))
        self.data["score_history"] = self.data["score_history"][-100:]

        self._save()

    def get_summary(self) -> dict:
        return {
            "total_processed": self.data["total_processed"],
            "by_grade": self.data["by_grade"],
            "by_theme": self.data["by_theme"],
            "by_tag": self.data["by_tag"],
            "session_count": len(self.data["sessions"]),
            "avg_score_trend": self.data["score_history"][-10:],
        }

    def reset(self):
        self.data = self._load.__func__(self)  # reload defaults
        self._save()
