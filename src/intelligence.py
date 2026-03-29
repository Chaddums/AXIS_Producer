"""Intelligence Pipeline — local pre/post-LLM filtering, dedup, and consolidation.

Sits between the transcript buffer and the LLM, and between the LLM output
and downstream consumers. Orchestrates existing local tools (triage scoring,
FTS5 search, feedback weights, word-overlap dedup) to minimize token waste
and maximize output quality.

The pipeline does NOT replace LLM calls — it makes them smarter by:
1. Telling the LLM what was already captured (context hints)
2. Filtering low-value items from LLM output (triage scoring)
3. Deduplicating items against the session and historical DB
4. Routing items to themes locally (no LLM needed)
5. Consolidating session data for the end-of-session synthesis
"""

import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime

from digest_db import DigestDB, DEFAULT_DB_PATH
from user_db import UserDB
from triage import score_item, route_to_theme, load_training


def normalize_text(text: str) -> str:
    """Normalize text for comparison: lowercase, strip punctuation, collapse whitespace."""
    text = text.lower().strip()
    text = re.sub(r'[^\w\s]', '', text)
    text = re.sub(r'\s+', ' ', text)
    return text


def content_hash(text: str) -> str:
    """SHA-256 hash of normalized text for exact-match dedup."""
    return hashlib.sha256(normalize_text(text).encode()).hexdigest()[:16]


def extract_terms(text: str) -> list[str]:
    """Extract significant terms from text (4+ char words, no stop words)."""
    STOP = frozenset([
        "about", "also", "been", "before", "being", "between", "both",
        "could", "does", "doing", "done", "each", "even", "from",
        "have", "here", "into", "just", "like", "make", "many", "more",
        "most", "much", "need", "only", "other", "over", "said", "same",
        "should", "some", "such", "than", "that", "their", "them", "then",
        "there", "these", "they", "this", "through", "very", "want",
        "what", "when", "where", "which", "while", "will", "with", "would",
    ])
    words = re.findall(r'\b\w{4,}\b', text.lower())
    return [w for w in words if w not in STOP]


def word_overlap(text_a: str, text_b: str, threshold: int = 3) -> bool:
    """Check if two texts share enough significant words to be considered similar."""
    terms_a = set(extract_terms(text_a))
    terms_b = set(extract_terms(text_b))
    if len(terms_a) < 2 or len(terms_b) < 2:
        return False
    return len(terms_a & terms_b) >= threshold


class IntelligencePipeline:
    """Local intelligence pipeline for pre/post-LLM filtering and consolidation."""

    def __init__(self, db_path: str = DEFAULT_DB_PATH, verbose: bool = False):
        self._digest_db = DigestDB(db_path)
        self._user_db = UserDB()
        self._theme_keywords = load_training()
        self._feedback_weights = None
        self._verbose = verbose

        # Session state
        self._session_items: list[dict] = []    # all items this session
        self._session_hashes: set[str] = set()  # content hashes for fast dedup
        self._batch_count = 0

        # Load feedback weights
        self.refresh_weights()

    def refresh_weights(self):
        """Reload feedback weights from user DB."""
        try:
            self._feedback_weights = self._user_db.get_effective_weights()
        except Exception:
            self._feedback_weights = {}

    def build_context_hint(self) -> str:
        """Build a summary of already-captured items for the LLM prompt.

        Tells the LLM "you already captured these, don't repeat them."
        Returns a formatted string to append to the user message.
        """
        if not self._session_items:
            return ""

        # Group by category, show recent items
        by_cat = defaultdict(list)
        for item in self._session_items[-30:]:  # last 30 items max
            by_cat[item.get("tag", "OTHER")].append(item["text"])

        lines = ["\n\n[ALREADY CAPTURED — do not repeat these:]"]
        for cat, texts in by_cat.items():
            lines.append(f"  {cat}:")
            for t in texts[-5:]:  # last 5 per category
                lines.append(f"    - {t[:80]}")

        return "\n".join(lines)

    def filter_items(self, raw_items: list[tuple[str, str]],
                     session_date: str = "") -> list[dict]:
        """Post-LLM filter: triage, dedup, score, theme each item.

        Args:
            raw_items: list of (category, text) tuples from LLM output
            session_date: date string for DB storage

        Returns:
            list of enriched item dicts that survived filtering
        """
        if not session_date:
            session_date = datetime.now().strftime("%Y-%m-%d")
        batch_time = datetime.now().strftime("%H:%M")
        self._batch_count += 1

        TAG_MAP = {
            "Decisions Locked": "DECISION",
            "Ideas Generated": "IDEA",
            "Open Questions": "QUESTION",
            "Action Items": "ACTION",
            "Watch List": "WATCH",
            "Blockers": "BLOCKER",
            "Key Discussion": "DISCUSSION",
        }

        accepted = []
        dropped_dedup = 0
        dropped_stale = 0
        dropped_feedback = 0

        for category, text in raw_items:
            text = text.strip()
            if not text:
                continue

            # Build item dict for triage scoring
            tag = TAG_MAP.get(category, "DISCUSSION")
            item_dict = {"tag": tag, "text": text, "time": batch_time}

            # 1. Exact dedup within session
            h = content_hash(text)
            if h in self._session_hashes:
                dropped_dedup += 1
                if self._verbose:
                    print(f"    [intel] dedup (exact): {text[:50]}...")
                continue

            # 2. Similarity dedup within session (word overlap)
            is_similar = False
            for existing in self._session_items:
                if word_overlap(text, existing["text"]):
                    dropped_dedup += 1
                    is_similar = True
                    if self._verbose:
                        print(f"    [intel] dedup (similar): {text[:50]}...")
                    break
            if is_similar:
                continue

            # 3. FTS5 dedup against historical DB
            try:
                terms = extract_terms(text)
                if len(terms) >= 2:
                    query = " ".join(terms[:5])  # search with top 5 terms
                    matches = self._digest_db.search(query, limit=3)
                    for match in matches:
                        if word_overlap(text, match["text"], threshold=4):
                            dropped_dedup += 1
                            is_similar = True
                            if self._verbose:
                                print(f"    [intel] dedup (historical): {text[:50]}...")
                            break
            except Exception:
                pass
            if is_similar:
                continue

            # 4. Triage scoring
            result = score_item(item_dict, self._theme_keywords, self._feedback_weights)

            # 5. Drop stale items
            if result["grade"] == "stale":
                dropped_stale += 1
                if self._verbose:
                    print(f"    [intel] dropped (stale, score={result['score']}): {text[:50]}...")
                continue

            # 6. Check feedback suppression
            if self._feedback_weights:
                item_terms = extract_terms(text)
                avg_weight = 0
                weighted_count = 0
                for term in item_terms:
                    if term in self._feedback_weights:
                        avg_weight += self._feedback_weights[term].get("weight", 0)
                        weighted_count += 1
                if weighted_count > 0 and avg_weight / weighted_count < -30:
                    dropped_feedback += 1
                    if self._verbose:
                        print(f"    [intel] dropped (feedback suppressed): {text[:50]}...")
                    continue

            # 7. Route to theme
            theme = route_to_theme(text, self._theme_keywords, self._feedback_weights)

            # Accept this item
            enriched = {
                "tag": tag,
                "category": category,
                "text": text,
                "theme": theme,
                "triage_score": result["score"],
                "triage_grade": result["grade"],
                "content_hash": h,
                "term_vector": json.dumps(extract_terms(text)[:10]),
                "session_date": session_date,
                "batch_time": batch_time,
                "batch_number": self._batch_count,
            }
            accepted.append(enriched)
            self._session_items.append(enriched)
            self._session_hashes.add(h)

        if self._verbose:
            total = len(raw_items)
            print(f"  [intel] batch {self._batch_count}: {len(accepted)}/{total} accepted "
                  f"(dedup={dropped_dedup}, stale={dropped_stale}, suppressed={dropped_feedback})")

        return accepted

    def consolidate_session(self) -> dict:
        """At session end: produce a consolidated summary of all items.

        Returns a dict with:
        - items: deduplicated, scored, themed items
        - by_theme: items grouped by theme
        - by_grade: items grouped by triage grade
        - stats: summary counts
        - top_items: highest-scoring items
        """
        if not self._session_items:
            return {
                "items": [], "by_theme": {}, "by_grade": {},
                "stats": {"total": 0}, "top_items": [],
            }

        # Group by theme
        by_theme = defaultdict(list)
        for item in self._session_items:
            by_theme[item.get("theme", "Other")].append(item)

        # Group by grade
        by_grade = defaultdict(list)
        for item in self._session_items:
            by_grade[item.get("triage_grade", "ungraded")].append(item)

        # Top items (highest score)
        sorted_items = sorted(self._session_items, key=lambda i: i.get("triage_score", 0), reverse=True)
        top_items = sorted_items[:10]

        # Stats
        grade_counts = {g: len(items) for g, items in by_grade.items()}
        theme_counts = {t: len(items) for t, items in by_theme.items()}
        tag_counts = Counter(i.get("tag", "?") for i in self._session_items)

        stats = {
            "total": len(self._session_items),
            "batches": self._batch_count,
            "by_grade": grade_counts,
            "by_theme": theme_counts,
            "by_tag": dict(tag_counts),
            "avg_score": round(
                sum(i.get("triage_score", 0) for i in self._session_items) / len(self._session_items), 1
            ) if self._session_items else 0,
        }

        return {
            "items": self._session_items,
            "by_theme": dict(by_theme),
            "by_grade": dict(by_grade),
            "stats": stats,
            "top_items": top_items,
        }

    def format_context_for_synthesis(self) -> str:
        """Format the consolidated session data as a structured preamble for the LLM synthesis.

        This gives the LLM a head start so it can focus on communication dynamics
        rather than re-extracting items.
        """
        consolidated = self.consolidate_session()
        if not consolidated["items"]:
            return ""

        lines = ["## Pre-Analysis (from local intelligence pipeline)\n"]

        # Stats overview
        stats = consolidated["stats"]
        lines.append(f"Session: {stats['batches']} batches, {stats['total']} items captured")
        lines.append(f"Average triage score: {stats['avg_score']}/100")
        lines.append(f"Grade distribution: {stats['by_grade']}")
        lines.append("")

        # Items by theme
        lines.append("### Items by Theme\n")
        for theme, items in consolidated["by_theme"].items():
            lines.append(f"**{theme}** ({len(items)} items):")
            for item in sorted(items, key=lambda i: -i.get("triage_score", 0))[:5]:
                score = item.get("triage_score", 0)
                grade = item.get("triage_grade", "?")
                lines.append(f"  - [{item['tag']}] (score={score}, {grade}) {item['text'][:100]}")
            if len(items) > 5:
                lines.append(f"  ... and {len(items) - 5} more")
            lines.append("")

        # Top actionable items
        actionable = consolidated["by_grade"].get("actionable", [])
        if actionable:
            lines.append("### Top Actionable Items\n")
            for item in actionable[:8]:
                lines.append(f"  - [{item['tag']}] {item['text'][:120]}")
            lines.append("")

        # Open blockers
        needs_context = consolidated["by_grade"].get("needs-context", [])
        blockers = [i for i in consolidated["items"] if i.get("tag") == "BLOCKER"]
        if blockers:
            lines.append("### Blockers\n")
            for b in blockers:
                lines.append(f"  - {b['text'][:120]}")
            lines.append("")

        return "\n".join(lines)

    def index_session(self):
        """Write all session items to the digest DB."""
        if not self._session_items:
            return
        items_for_db = []
        for item in self._session_items:
            items_for_db.append({
                "session_date": item.get("session_date", datetime.now().strftime("%Y-%m-%d")),
                "batch_time": item.get("batch_time", ""),
                "tag": item.get("tag", ""),
                "theme": item.get("theme", ""),
                "text": item.get("text", ""),
                "triage_score": item.get("triage_score", 0),
                "triage_grade": item.get("triage_grade", ""),
            })
        self._digest_db.insert_items(items_for_db)

    def save_session_summary(self, duration_minutes: int = 0,
                             llm_tokens_used: int = 0,
                             report_path: str = ""):
        """Save a session summary record for trend tracking."""
        consolidated = self.consolidate_session()
        stats = consolidated["stats"]
        self._digest_db.insert_session_summary(
            session_date=datetime.now().strftime("%Y-%m-%d"),
            duration_minutes=duration_minutes,
            total_items=stats["total"],
            items_by_grade=json.dumps(stats["by_grade"]),
            items_by_theme=json.dumps(stats["by_theme"]),
            top_items=json.dumps([
                {"tag": i["tag"], "text": i["text"][:100], "score": i["triage_score"]}
                for i in consolidated["top_items"]
            ]),
            llm_tokens_used=llm_tokens_used,
            report_path=report_path,
        )

    def reset(self):
        """Reset session state for a new session."""
        self._session_items = []
        self._session_hashes = set()
        self._batch_count = 0
        self.refresh_weights()

    def close(self):
        """Clean up resources."""
        self._digest_db.close()
        self._user_db.close()
