"""Blocker Tracker — detects, tracks, and escalates blockers across sessions.

A blocker is different from an action item: it's a dependency. Someone can't
proceed until something else happens. Blockers have owners (who's blocked),
dependencies (what they're waiting on), and they escalate over time.

Detection sources:
1. Claude's producer prompt (new BLOCKER section)
2. Transcript keyword scanning (fallback, catches what Claude misses)
3. Incoming Slack/email that mentions a known blocker topic

Persistence: blockers table in digest.db alongside items.
"""

import os
import re
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from digest_db import DigestDB, DEFAULT_DB_PATH


# ---------------------------------------------------------------------------
# Blocker data model
# ---------------------------------------------------------------------------

@dataclass
class Blocker:
    """A tracked blocker — someone is waiting on something."""
    id: int = 0                    # DB row id
    text: str = ""                 # what the blocker is
    owner: str = ""                # who is blocked (if mentioned)
    dependency: str = ""           # what they're waiting on
    theme: str = ""                # project theme
    source_session: str = ""       # session date when first detected
    source_time: str = ""          # HH:MM when detected
    status: str = "open"           # open / resolved / stale
    severity: str = "normal"       # normal / critical
    mentions: int = 1              # how many times this has come up
    last_mentioned: str = ""       # last session/time it was mentioned
    resolved_at: str = ""          # when it was resolved
    created_at: str = ""

    @property
    def age_days(self) -> int:
        try:
            created = datetime.strptime(self.created_at[:10], "%Y-%m-%d")
            return (datetime.now() - created).days
        except (ValueError, TypeError):
            return 0

    @property
    def is_escalated(self) -> bool:
        """Blockers escalate if open for 2+ days or mentioned 3+ times."""
        return self.age_days >= 2 or self.mentions >= 3

    @property
    def priority_score(self) -> int:
        """Dynamic priority score for sorting."""
        score = 50
        if self.severity == "critical":
            score += 30
        score += min(self.age_days * 5, 25)     # +5/day, cap 25
        score += min(self.mentions * 3, 15)      # +3/mention, cap 15
        return min(100, score)

    def format_display(self) -> str:
        age = f"{self.age_days}d" if self.age_days > 0 else "new"
        sev = " CRITICAL" if self.severity == "critical" else ""
        owner = f" ({self.owner})" if self.owner else ""
        dep = f" — waiting on: {self.dependency}" if self.dependency else ""
        mentions = f" [{self.mentions}x]" if self.mentions > 1 else ""
        return (f"[{self.status.upper()}{sev}] {self.text}{owner}{dep}\n"
                f"  Priority: {self.priority_score}/100 | Age: {age}{mentions} | "
                f"Theme: {self.theme}")


# ---------------------------------------------------------------------------
# Blocker detection from text
# ---------------------------------------------------------------------------

# Phrases that indicate someone is blocked
BLOCKER_PHRASES = [
    r"blocked on",
    r"blocked by",
    r"waiting on",
    r"waiting for",
    r"(?:can'?t|cannot|can not) (?:do|start|finish|proceed|continue|work on|test|ship|merge|build|deploy) (?:until|without|before)",
    r"depends on",
    r"dependent on",
    r"need (?:\w+ )?(?:from|before)",
    r"stuck on",
    r"stuck waiting",
    r"holding up",
    r"held up by",
    r"(?:total |hard |ship |release |critical )?blocker",
    r"bottleneck",
    r"gated on",
    r"gated by",
    r"prerequisite",
]

# Phrases that indicate a blocker was resolved
RESOLUTION_PHRASES = [
    r"unblocked",
    r"no longer blocked",
    r"resolved the",
    r"fixed the blocker",
    r"got (?:past|around|through) the",
    r"(?:that|this) is (?:done|fixed|resolved|unblocked)",
    r"we(?:'re| are) good on",
    r"good to go on",
]

# Phrases indicating critical severity
CRITICAL_PHRASES = [
    r"completely blocked",
    r"hard blocker",
    r"total blocker",
    r"nothing can happen until",
    r"everything depends on",
    r"showstopper",
    r"ship blocker",
    r"release blocker",
    r"critical blocker",
    r"can'?t ship",
    r"dead in the water",
]

_BLOCKER_RE = re.compile("|".join(BLOCKER_PHRASES), re.IGNORECASE)
_RESOLUTION_RE = re.compile("|".join(RESOLUTION_PHRASES), re.IGNORECASE)
_CRITICAL_RE = re.compile("|".join(CRITICAL_PHRASES), re.IGNORECASE)


def detect_blockers_in_text(text: str) -> list[dict]:
    """Scan text for blocker-like phrases. Returns list of detected blockers.

    Each result has: text, is_resolution, is_critical
    """
    results = []
    lines = text.split("\n")

    for line in lines:
        line = line.strip()
        if not line or len(line) < 10:
            continue

        # Check for resolution first
        if _RESOLUTION_RE.search(line):
            results.append({
                "text": line,
                "is_resolution": True,
                "is_critical": False,
            })
            continue

        # Check for new blocker
        if _BLOCKER_RE.search(line):
            is_critical = bool(_CRITICAL_RE.search(line))
            results.append({
                "text": line,
                "is_resolution": False,
                "is_critical": is_critical,
            })

    return results


def extract_owner_and_dependency(text: str) -> tuple[str, str]:
    """Try to extract who is blocked and what they're waiting on.

    Very rough heuristic — looks for patterns like:
    "I'm blocked on X", "Stu is waiting for Y", "blocked on Adam's review"
    """
    owner = ""
    dependency = ""

    # Owner patterns: "I'm/I am", "NAME is", "we're/we are"
    owner_match = re.search(
        r"(I'?m|I am|we'?re|we are|(\w+) (?:is|are)) (?:blocked|waiting|stuck)",
        text, re.IGNORECASE
    )
    if owner_match:
        if owner_match.group(1).lower().startswith(("i'", "i a")):
            owner = "me"
        elif owner_match.group(1).lower().startswith(("we'", "we a")):
            owner = "team"
        elif owner_match.group(2):
            owner = owner_match.group(2)

    # Dependency patterns: "on X", "for X", "until X"
    dep_match = re.search(
        r"(?:blocked on|waiting (?:on|for)|stuck on|depends on|until|need(?:s)?) (.+?)(?:\.|$|,| so )",
        text, re.IGNORECASE
    )
    if dep_match:
        dependency = dep_match.group(1).strip()
        # Trim to reasonable length
        if len(dependency) > 100:
            dependency = dependency[:97] + "..."

    return owner, dependency


# ---------------------------------------------------------------------------
# DB persistence — blockers table in digest.db
# ---------------------------------------------------------------------------

class BlockerDB:
    """Manages the blockers table alongside the existing digest items table."""

    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = db_path
        self._conn = None

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path)
            self._conn.row_factory = sqlite3.Row
            self._ensure_table()
        return self._conn

    def _ensure_table(self):
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS blockers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL,
                owner TEXT NOT NULL DEFAULT '',
                dependency TEXT NOT NULL DEFAULT '',
                theme TEXT NOT NULL DEFAULT '',
                source_session TEXT NOT NULL DEFAULT '',
                source_time TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'open',
                severity TEXT NOT NULL DEFAULT 'normal',
                mentions INTEGER NOT NULL DEFAULT 1,
                last_mentioned TEXT NOT NULL DEFAULT '',
                resolved_at TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
        """)
        self._conn.commit()

    def add_blocker(self, blocker: Blocker) -> int:
        conn = self._connect()
        cursor = conn.execute(
            "INSERT INTO blockers (text, owner, dependency, theme, source_session, "
            "source_time, status, severity, mentions, last_mentioned) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (blocker.text, blocker.owner, blocker.dependency, blocker.theme,
             blocker.source_session, blocker.source_time, blocker.status,
             blocker.severity, blocker.mentions, blocker.last_mentioned),
        )
        conn.commit()
        return cursor.lastrowid

    def update_blocker(self, blocker_id: int, **kwargs):
        conn = self._connect()
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        vals = list(kwargs.values()) + [blocker_id]
        conn.execute(f"UPDATE blockers SET {sets} WHERE id = ?", vals)
        conn.commit()

    def resolve_blocker(self, blocker_id: int):
        self.update_blocker(blocker_id,
                            status="resolved",
                            resolved_at=datetime.now().isoformat())

    def bump_mentions(self, blocker_id: int):
        conn = self._connect()
        conn.execute(
            "UPDATE blockers SET mentions = mentions + 1, "
            "last_mentioned = ? WHERE id = ?",
            (datetime.now().isoformat(), blocker_id),
        )
        conn.commit()

    def get_open_blockers(self) -> list[Blocker]:
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM blockers WHERE status = 'open' ORDER BY severity DESC, mentions DESC"
        ).fetchall()
        return [self._row_to_blocker(r) for r in rows]

    def get_all_blockers(self, limit: int = 50) -> list[Blocker]:
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM blockers ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [self._row_to_blocker(r) for r in rows]

    def find_similar(self, text: str, threshold: int = 3) -> Blocker | None:
        """Find an existing open blocker that matches the new text.

        Uses keyword overlap — if 3+ significant words match, it's the same blocker.
        """
        words = set(re.findall(r'\b\w{4,}\b', text.lower()))
        if len(words) < 2:
            return None

        for blocker in self.get_open_blockers():
            blocker_words = set(re.findall(r'\b\w{4,}\b', blocker.text.lower()))
            overlap = words & blocker_words
            if len(overlap) >= threshold:
                return blocker

        return None

    def get_stats(self) -> dict:
        conn = self._connect()
        total = conn.execute("SELECT COUNT(*) FROM blockers").fetchone()[0]
        open_count = conn.execute(
            "SELECT COUNT(*) FROM blockers WHERE status = 'open'"
        ).fetchone()[0]
        critical = conn.execute(
            "SELECT COUNT(*) FROM blockers WHERE status = 'open' AND severity = 'critical'"
        ).fetchone()[0]
        resolved = conn.execute(
            "SELECT COUNT(*) FROM blockers WHERE status = 'resolved'"
        ).fetchone()[0]
        avg_age = conn.execute(
            "SELECT AVG(julianday('now') - julianday(created_at)) "
            "FROM blockers WHERE status = 'open'"
        ).fetchone()[0]

        return {
            "total": total,
            "open": open_count,
            "critical": critical,
            "resolved": resolved,
            "avg_age_days": round(avg_age, 1) if avg_age else 0,
        }

    @staticmethod
    def _row_to_blocker(row) -> Blocker:
        return Blocker(
            id=row["id"],
            text=row["text"],
            owner=row["owner"],
            dependency=row["dependency"],
            theme=row["theme"],
            source_session=row["source_session"],
            source_time=row["source_time"],
            status=row["status"],
            severity=row["severity"],
            mentions=row["mentions"],
            last_mentioned=row["last_mentioned"],
            resolved_at=row["resolved_at"],
            created_at=row["created_at"],
        )

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None


# ---------------------------------------------------------------------------
# Blocker Tracker — orchestrates detection, dedup, escalation
# ---------------------------------------------------------------------------

class BlockerTracker:
    """Processes transcript batches and incoming messages for blockers.

    on_new_blocker(blocker: Blocker) — fired when a new blocker is detected
    on_blocker_escalated(blocker: Blocker) — fired when a blocker escalates
    on_blocker_resolved(blocker: Blocker) — fired when a blocker is resolved
    """

    def __init__(self, on_new_blocker=None,
                 on_blocker_escalated=None,
                 on_blocker_resolved=None,
                 db_path: str = DEFAULT_DB_PATH,
                 verbose: bool = False):
        self.on_new_blocker = on_new_blocker
        self.on_blocker_escalated = on_blocker_escalated
        self.on_blocker_resolved = on_blocker_resolved
        self.db_path = db_path
        self.verbose = verbose

        self._db = BlockerDB(db_path)
        self._theme_keywords = None

    def _get_theme_keywords(self):
        if self._theme_keywords is None:
            from triage import load_training
            self._theme_keywords = load_training()
        return self._theme_keywords

    def _route_theme(self, text: str) -> str:
        from triage import route_to_theme
        return route_to_theme(text, self._get_theme_keywords())

    def process_transcript_batch(self, transcript_lines: list[str],
                                  session_date: str = "",
                                  batch_time: str = ""):
        """Scan a batch of transcript lines for blockers.

        Called after each Claude producer batch, or on raw transcript.
        """
        full_text = "\n".join(transcript_lines)
        detections = detect_blockers_in_text(full_text)

        if not detections and self.verbose:
            return

        session_date = session_date or datetime.now().strftime("%Y-%m-%d %H:%M")
        batch_time = batch_time or datetime.now().strftime("%H:%M")

        for det in detections:
            if det["is_resolution"]:
                self._handle_resolution(det["text"])
            else:
                self._handle_new_blocker(
                    det["text"], det["is_critical"],
                    session_date, batch_time,
                )

    def process_producer_notes(self, notes: str, session_date: str = "",
                                batch_time: str = ""):
        """Process Claude's producer output for the Blockers section.

        Looks for ## Blockers section in the structured notes.
        """
        # Extract blockers section if present
        blocker_section = re.search(
            r"##\s*Blockers\s*\n((?:- .+\n?)+)", notes, re.IGNORECASE
        )

        if blocker_section:
            lines = blocker_section.group(1).strip().split("\n")
            for line in lines:
                line = line.strip().lstrip("- ").strip()
                if line and line != "[nothing to report]":
                    is_critical = bool(_CRITICAL_RE.search(line))
                    self._handle_new_blocker(
                        line, is_critical,
                        session_date or datetime.now().strftime("%Y-%m-%d %H:%M"),
                        batch_time or datetime.now().strftime("%H:%M"),
                    )

        # Also scan all notes for blocker language (catches ones Claude
        # might have filed under Action Items or Watch List)
        detections = detect_blockers_in_text(notes)
        for det in detections:
            if det["is_resolution"]:
                self._handle_resolution(det["text"])
            elif not blocker_section:  # avoid double-counting
                self._handle_new_blocker(
                    det["text"], det["is_critical"],
                    session_date or datetime.now().strftime("%Y-%m-%d %H:%M"),
                    batch_time or datetime.now().strftime("%H:%M"),
                )

    def check_incoming_message(self, source: str, text: str) -> Blocker | None:
        """Check if an incoming Slack/email message relates to an open blocker.

        Returns the matched blocker if found (for focus advisor escalation).
        """
        # Check for resolution language first
        if _RESOLUTION_RE.search(text):
            existing = self._db.find_similar(text, threshold=2)
            if existing:
                self._handle_resolution(text, existing)
                return None

        # Check if this message matches an open blocker
        existing = self._db.find_similar(text, threshold=2)
        if existing:
            self._db.bump_mentions(existing.id)
            was_escalated = existing.is_escalated

            # Refresh from DB
            existing.mentions += 1
            if existing.is_escalated and not was_escalated:
                if self.verbose:
                    print(f"  [blocker] escalated: {existing.text[:60]}")
                if self.on_blocker_escalated:
                    self.on_blocker_escalated(existing)

            return existing

        # Check if message contains new blocker language
        detections = detect_blockers_in_text(text)
        for det in detections:
            if not det["is_resolution"]:
                blocker = self._handle_new_blocker(
                    det["text"], det["is_critical"],
                    f"via {source}", datetime.now().strftime("%H:%M"),
                )
                return blocker

        return None

    def _handle_new_blocker(self, text: str, is_critical: bool,
                             session_date: str, batch_time: str) -> Blocker | None:
        """Process a newly detected blocker — dedup or create."""
        # Check for duplicate
        existing = self._db.find_similar(text)
        if existing:
            self._db.bump_mentions(existing.id)
            if is_critical and existing.severity != "critical":
                self._db.update_blocker(existing.id, severity="critical")
            if self.verbose:
                print(f"  [blocker] duplicate, bumped mentions: {text[:60]}")
            return existing

        owner, dependency = extract_owner_and_dependency(text)
        theme = self._route_theme(text)

        blocker = Blocker(
            text=text,
            owner=owner,
            dependency=dependency,
            theme=theme,
            source_session=session_date,
            source_time=batch_time,
            severity="critical" if is_critical else "normal",
            last_mentioned=datetime.now().isoformat(),
        )

        blocker.id = self._db.add_blocker(blocker)

        if self.verbose:
            sev = " [CRITICAL]" if is_critical else ""
            print(f"  [blocker] new{sev}: {text[:60]}")

        if self.on_new_blocker:
            self.on_new_blocker(blocker)

        return blocker

    def _handle_resolution(self, text: str, existing: Blocker = None):
        """Handle detected blocker resolution."""
        if existing is None:
            existing = self._db.find_similar(text, threshold=2)

        if existing and existing.status == "open":
            self._db.resolve_blocker(existing.id)
            existing.status = "resolved"

            if self.verbose:
                print(f"  [blocker] resolved: {existing.text[:60]}")

            if self.on_blocker_resolved:
                self.on_blocker_resolved(existing)

    def get_open_blockers(self) -> list[Blocker]:
        """Get all open blockers sorted by priority."""
        blockers = self._db.get_open_blockers()
        blockers.sort(key=lambda b: b.priority_score, reverse=True)
        return blockers

    def get_stats(self) -> dict:
        return self._db.get_stats()

    def close(self):
        self._db.close()
