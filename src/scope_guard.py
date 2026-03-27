"""Scope Guard — catches scope creep and overcommitment in real time.

Two jobs:
1. **Scope creep** — When someone proposes something that matches a CUT item
   or is clearly out-of-scope, flag it: "This sounds like multiplayer, which
   was explicitly cut."

2. **Capacity check** — When someone volunteers for work ("I'll just do X",
   "let me add Y"), check their current load: open action items, open blockers
   they own, age of existing commitments. If they're overloaded, flag it:
   "You already have 8 open action items including 2 from last week."

Sources:
- ALPHA_ROADMAP.md for CUT items and scope definition
- digest_db for open ACTION items per person
- blocker_tracker for open blockers per person
- VCS monitor for recent commit activity (are they actively shipping?)
"""

import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from blocker_tracker import BlockerDB
from digest_db import DigestDB, DEFAULT_DB_PATH


# ---------------------------------------------------------------------------
# Scope Guard data model
# ---------------------------------------------------------------------------

@dataclass
class ScopeAlert:
    """A scope or capacity alert."""
    type: str           # "scope_creep", "overcommit", "cut_item"
    severity: str       # "warning", "critical"
    message: str        # what to show the user
    detail: str         # supporting evidence
    trigger_text: str   # the text that triggered this alert
    timestamp: str


# ---------------------------------------------------------------------------
# Roadmap parser
# ---------------------------------------------------------------------------

class RoadmapState:
    """Parsed state of ALPHA_ROADMAP.md."""

    def __init__(self, roadmap_path: str = ""):
        self.cut_items: list[str] = []
        self.todo_items: list[str] = []
        self.wip_items: list[str] = []
        self.done_items: list[str] = []
        self.scope_summary: str = ""
        self._path = roadmap_path

        if roadmap_path and os.path.exists(roadmap_path):
            self._parse(roadmap_path)

    def _parse(self, path: str):
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        # Extract scope summary (first paragraph after "What IS the Alpha")
        scope_match = re.search(
            r"## What IS the Alpha\s*\n+(.*?)(?:\n---|\n##)", content, re.DOTALL
        )
        if scope_match:
            self.scope_summary = scope_match.group(1).strip()

        # Extract CUT section — grab everything between the header and the next ---
        cut_start = content.find("## Explicitly CUT")
        if cut_start >= 0:
            # Find the next --- separator after the CUT header
            rest = content[cut_start:]
            # Skip past the header line itself
            lines_after = rest.split("\n")[1:]
            for line in lines_after:
                stripped = line.strip()
                if stripped == "---" or stripped.startswith("## "):
                    break
                if stripped.startswith("- "):
                    item = stripped[2:].strip()
                    if item:
                        self.cut_items.append(item)

        # Extract table items by status (only from proper markdown tables)
        status_pattern = re.compile(
            r"\|\s*\d+\s*\|\s*(.+?)\s*\|\s*`(DONE|WIP|TODO|CUT)`\s*\|",
        )
        for match in status_pattern.finditer(content):
            feature = match.group(1).strip()
            status = match.group(2)
            if status == "CUT":
                self.cut_items.append(feature)
            elif status == "TODO":
                self.todo_items.append(feature)
            elif status == "WIP":
                self.wip_items.append(feature)
            elif status == "DONE":
                self.done_items.append(feature)

    def reload(self):
        if self._path:
            self.cut_items.clear()
            self.todo_items.clear()
            self.wip_items.clear()
            self.done_items.clear()
            if os.path.exists(self._path):
                self._parse(self._path)


# ---------------------------------------------------------------------------
# Volunteering / commitment detection
# ---------------------------------------------------------------------------

# "I'll just do X", "let me add Y", "I can handle X", etc.
VOLUNTEER_PHRASES = [
    r"(?:I'?ll|I will|I can|let me|I'?m going to|I'?m gonna|I should) (?:just |quickly |also )?"
    r"(?:do|add|build|fix|implement|create|make|write|handle|tackle|knock out|bang out|ship|push|get)",
    r"(?:I'?ll|let me) (?:just )?take (?:care of|on|a look at|a crack at|a stab at)",
    r"(?:I can|I could) (?:probably |easily |just )?(?:squeeze|fit|sneak|slip|throw) (?:that |this |it )?in",
    r"shouldn'?t (?:take|be) (?:long|too hard|that hard|too bad|much)",
    r"(?:I'?ll|I can) (?:just )?knock (?:that|this|it) out",
    r"how hard can it be",
    r"real quick",
    r"(?:while I'?m|since I'?m) (?:at it|in there|already)",
]

# Scope creep phrases — suggesting new features or expanding scope
SCOPE_CREEP_PHRASES = [
    r"(?:we|I) (?:should|could|might) (?:also |maybe )?add",
    r"what if we (?:also |just )?",
    r"wouldn'?t it be (?:cool|nice|great|better) (?:if|to)",
    r"(?:we|I) (?:could|should) (?:also |probably )?(?:do|build|add|implement)",
    r"shouldn'?t we (?:also |just )?(?:add|build|do|implement|have|include)",
    r"while we'?re at it",
    r"one more thing",
    r"oh (?:also|and|wait)",
    r"(?:stretch|bonus|extra|nice.to.have) (?:goal|feature|idea)",
    r"(?:we|I) (?:need|want) (?:to add|to build|to include)",
    r"why don'?t we (?:also |just )?",
]

_VOLUNTEER_RE = re.compile("|".join(VOLUNTEER_PHRASES), re.IGNORECASE)
_SCOPE_CREEP_RE = re.compile("|".join(SCOPE_CREEP_PHRASES), re.IGNORECASE)


def detect_volunteering(text: str) -> list[str]:
    """Find lines where someone is volunteering for work."""
    results = []
    for line in text.split("\n"):
        line = line.strip()
        if len(line) < 10:
            continue
        if _VOLUNTEER_RE.search(line):
            results.append(line)
    return results


def detect_scope_creep(text: str) -> list[str]:
    """Find lines that suggest scope expansion."""
    results = []
    for line in text.split("\n"):
        line = line.strip()
        if len(line) < 10:
            continue
        if _SCOPE_CREEP_RE.search(line):
            results.append(line)
    return results


# ---------------------------------------------------------------------------
# CUT item matching
# ---------------------------------------------------------------------------

def _extract_keywords(text: str) -> set[str]:
    """Extract meaningful keywords for matching."""
    words = re.findall(r'\b\w{3,}\b', text.lower())
    stop = {"the", "and", "for", "with", "that", "this", "from", "have",
            "not", "are", "was", "were", "been", "will", "can", "should",
            "could", "would", "just", "also", "need", "want", "like",
            "into", "about", "some", "them", "their", "what", "when",
            "only", "then", "than", "more", "very", "been", "being",
            "does", "did", "has", "had"}
    return {w for w in words if w not in stop}


def match_cut_items(text: str, cut_items: list[str],
                     threshold: int = 2) -> list[tuple[str, int]]:
    """Check if text matches any CUT item by keyword overlap.

    For short CUT items (1-2 words like "Multiplayer"), uses exact
    substring match. For longer items, uses keyword overlap.

    Returns list of (cut_item, overlap_count) for matches.
    """
    text_lower = text.lower()
    text_keywords = _extract_keywords(text)
    if len(text_keywords) < 1:
        return []

    matches = []
    for cut_item in cut_items:
        cut_keywords = _extract_keywords(cut_item)

        # Short CUT items (1-2 meaningful words): exact substring match
        if len(cut_keywords) <= 2:
            # Check if the core word(s) appear in the text
            core_words = [w for w in cut_keywords if len(w) >= 4]
            if core_words and all(w in text_lower for w in core_words):
                matches.append((cut_item, len(core_words) + 1))
                continue

        # Normal keyword overlap for longer items
        overlap = text_keywords & cut_keywords
        if len(overlap) >= threshold:
            matches.append((cut_item, len(overlap)))

    matches.sort(key=lambda x: -x[1])
    return matches


# ---------------------------------------------------------------------------
# Capacity assessment
# ---------------------------------------------------------------------------

@dataclass
class CapacitySnapshot:
    """Someone's current workload."""
    name: str
    open_actions: int
    stale_actions: int       # 5+ days old
    open_blockers: int
    blockers_they_own: int
    recent_commits: int      # last 48h
    oldest_action_days: int
    total_estimated_hours: float  # rough estimate

    @property
    def is_overloaded(self) -> bool:
        return (self.open_actions >= 5
                or self.stale_actions >= 3
                or self.total_estimated_hours >= 15)

    @property
    def load_description(self) -> str:
        parts = []
        if self.open_actions > 0:
            parts.append(f"{self.open_actions} open action items")
        if self.stale_actions > 0:
            parts.append(f"{self.stale_actions} stale ({self.oldest_action_days}d+)")
        if self.blockers_they_own > 0:
            parts.append(f"{self.blockers_they_own} blockers they own")
        if self.recent_commits > 0:
            parts.append(f"{self.recent_commits} commits in 48h")
        if not parts:
            return "plate looks clear"
        return ", ".join(parts)


# Rough hours estimate per item type
HOURS_PER_ACTION = 2.0
HOURS_PER_BLOCKER = 1.5


def assess_capacity(name: str = "",
                     db_path: str = DEFAULT_DB_PATH,
                     repo_path: str = "") -> CapacitySnapshot:
    """Assess someone's current workload from all available data.

    If name is empty, assesses the total team load.
    """
    name_lower = name.lower() if name else ""

    # Count open action items
    db = DigestDB(db_path)
    try:
        all_actions = db.search_by_tag("ACTION", limit=100)
    finally:
        db.close()

    # Filter by name if provided (rough — checks if name appears in text)
    if name_lower and name_lower not in ("me", "team", ""):
        actions = [a for a in all_actions
                   if name_lower in a.get("text", "").lower()]
    else:
        actions = all_actions

    open_actions = len(actions)

    # Count stale actions
    stale = 0
    oldest_days = 0
    for a in actions:
        try:
            session_date = a.get("session_date", "")[:10]
            age = (datetime.now() - datetime.strptime(session_date, "%Y-%m-%d")).days
            oldest_days = max(oldest_days, age)
            if age >= 5:
                stale += 1
        except (ValueError, TypeError):
            pass

    # Count blockers
    bdb = BlockerDB(db_path)
    try:
        all_blockers = bdb.get_open_blockers()
    finally:
        bdb.close()

    if name_lower and name_lower not in ("me", "team", ""):
        owned_blockers = [b for b in all_blockers
                          if name_lower in b.owner.lower()
                          or name_lower in b.text.lower()]
    else:
        owned_blockers = all_blockers

    # VCS activity
    recent_commits = 0
    if repo_path:
        try:
            from vcs_monitor import GitBackend
            backend = GitBackend(repo_path)
            since = datetime.now() - timedelta(hours=48)
            changes = backend.recent_changes(since, limit=50)
            if name_lower and name_lower not in ("me", "team", ""):
                changes = [c for c in changes
                           if name_lower in c.author.lower()]
            recent_commits = len(changes)
        except Exception:
            pass

    estimated_hours = (open_actions * HOURS_PER_ACTION
                       + len(owned_blockers) * HOURS_PER_BLOCKER)

    return CapacitySnapshot(
        name=name or "team",
        open_actions=open_actions,
        stale_actions=stale,
        open_blockers=len(all_blockers),
        blockers_they_own=len(owned_blockers),
        recent_commits=recent_commits,
        oldest_action_days=oldest_days,
        total_estimated_hours=estimated_hours,
    )


# ---------------------------------------------------------------------------
# ScopeGuard — the main orchestrator
# ---------------------------------------------------------------------------

class ScopeGuard:
    """Checks transcript text for scope creep and overcommitment.

    on_alert(alert: ScopeAlert) — fired when something needs flagging.
    """

    def __init__(self, roadmap_path: str = "",
                 on_alert=None,
                 db_path: str = DEFAULT_DB_PATH,
                 repo_path: str = "",
                 verbose: bool = False):
        self.on_alert = on_alert
        self.db_path = db_path
        self.repo_path = repo_path
        self.verbose = verbose

        self._roadmap = RoadmapState(roadmap_path)
        self._alerted_texts: set[str] = set()  # dedup by text prefix
        self.alerts_fired = 0

    def reload_roadmap(self):
        self._roadmap.reload()

    def check_transcript(self, text: str, session_date: str = "",
                          batch_time: str = ""):
        """Check a batch of transcript for scope/capacity issues."""
        timestamp = batch_time or datetime.now().strftime("%H:%M")

        # 1. Check for scope creep against CUT items
        scope_lines = detect_scope_creep(text)
        for line in scope_lines:
            cut_matches = match_cut_items(line, self._roadmap.cut_items)
            if cut_matches:
                best_cut, hits = cut_matches[0]
                self._fire_alert(ScopeAlert(
                    type="cut_item",
                    severity="critical" if hits >= 3 else "warning",
                    message=f"This sounds like a CUT item: \"{best_cut}\"",
                    detail=(f"Matched {hits} keywords against the CUT list.\n"
                            f"Roadmap says: explicitly not in alpha."),
                    trigger_text=line,
                    timestamp=timestamp,
                ))

        # 2. Check for volunteering + capacity
        volunteer_lines = detect_volunteering(text)
        for line in volunteer_lines:
            # Also check if this volunteers for a CUT item
            cut_matches = match_cut_items(line, self._roadmap.cut_items)
            if cut_matches:
                best_cut, hits = cut_matches[0]
                self._fire_alert(ScopeAlert(
                    type="cut_item",
                    severity="critical",
                    message=f"Volunteering for a CUT item: \"{best_cut}\"",
                    detail=f"This was explicitly cut from the alpha scope.",
                    trigger_text=line,
                    timestamp=timestamp,
                ))
                continue

            # Capacity check
            # Try to extract who is volunteering
            name = self._extract_volunteer_name(line)
            capacity = assess_capacity(
                name=name, db_path=self.db_path, repo_path=self.repo_path,
            )

            if capacity.is_overloaded:
                self._fire_alert(ScopeAlert(
                    type="overcommit",
                    severity="warning",
                    message=(f"{'You' if name in ('me', '') else name} already "
                             f"{'have' if name in ('me', '', 'team') else 'has'} "
                             f"~{capacity.total_estimated_hours:.0f}h of open work"),
                    detail=(f"Current load: {capacity.load_description}\n"
                            f"Adding more risks pushing existing commitments."),
                    trigger_text=line,
                    timestamp=timestamp,
                ))

    def check_producer_notes(self, notes: str, session_date: str = "",
                               batch_time: str = ""):
        """Check Claude's structured producer output for scope issues.

        Specifically looks at Ideas Generated and Action Items for CUT overlap.
        """
        timestamp = batch_time or datetime.now().strftime("%H:%M")

        # Extract ideas and new actions
        idea_pattern = re.compile(r"##\s*Ideas Generated\s*\n((?:- .+\n?)+)", re.IGNORECASE)
        action_pattern = re.compile(r"##\s*Action Items\s*\n((?:- .+\n?)+)", re.IGNORECASE)

        for pattern, item_type in [(idea_pattern, "idea"), (action_pattern, "action")]:
            match = pattern.search(notes)
            if not match:
                continue
            for line in match.group(1).strip().split("\n"):
                item_text = line.strip().lstrip("- ").strip()
                if not item_text:
                    continue

                cut_matches = match_cut_items(item_text, self._roadmap.cut_items)
                if cut_matches:
                    best_cut, hits = cut_matches[0]
                    self._fire_alert(ScopeAlert(
                        type="scope_creep",
                        severity="warning",
                        message=f"New {item_type} matches CUT item: \"{best_cut}\"",
                        detail=(f"The {item_type} \"{item_text[:60]}\" overlaps with "
                                f"an explicitly cut feature.\n"
                                f"Keyword overlap: {hits} words."),
                        trigger_text=item_text,
                        timestamp=timestamp,
                    ))

    def _extract_volunteer_name(self, text: str) -> str:
        """Try to extract who is volunteering from the text."""
        # "I'll" / "I can" / "let me" → "me"
        if re.search(r"\b(?:I'?ll|I will|I can|I'?m|let me)\b", text, re.IGNORECASE):
            return "me"
        # "NAME will" / "NAME can" / "NAME is going to"
        name_match = re.search(
            r"\b(\w+)\s+(?:will|can|is going to|'s gonna|should)\b",
            text, re.IGNORECASE
        )
        if name_match:
            name = name_match.group(1)
            # Filter out common non-names
            if name.lower() not in ("it", "this", "that", "we", "they",
                                      "someone", "somebody", "who", "what",
                                      "which", "there", "here"):
                return name
        return ""

    def _fire_alert(self, alert: ScopeAlert):
        """Fire an alert, with deduplication."""
        key = alert.trigger_text[:40].lower()
        if key in self._alerted_texts:
            return
        self._alerted_texts.add(key)

        # Cap dedup set
        if len(self._alerted_texts) > 200:
            self._alerted_texts = set(list(self._alerted_texts)[-100:])

        self.alerts_fired += 1

        if self.verbose:
            print(f"  [scope] [{alert.severity}] {alert.type}: {alert.message}")

        if self.on_alert:
            self.on_alert(alert)
