"""VCS Monitor — tracks version control activity to close the intent-vs-execution gap.

Periodically polls for recent commits/changelists, maps changed files to project
themes, and cross-references against ACTION/DECISION items in the digest DB to
detect progress, drift, and stalls.

Backend-agnostic: ships with GitBackend, designed so P4VBackend can slot in later.
"""

import abc
import os
import re
import subprocess
import threading
import time
from datetime import datetime, timedelta
from dataclasses import dataclass, field

from digest_db import DigestDB, DEFAULT_DB_PATH
from triage import route_to_theme, load_training


# ---------------------------------------------------------------------------
# Abstract backend — swap GitBackend for P4VBackend later
# ---------------------------------------------------------------------------

@dataclass
class VcsChange:
    """A single commit (git) or changelist (p4)."""
    id: str               # commit hash or changelist number
    author: str
    message: str
    timestamp: datetime
    files: list[str]      # list of changed file paths
    insertions: int = 0
    deletions: int = 0


class VcsBackend(abc.ABC):
    """Abstract interface for version control queries."""

    @abc.abstractmethod
    def recent_changes(self, since: datetime, limit: int = 50) -> list[VcsChange]:
        """Return changes since the given timestamp."""

    @abc.abstractmethod
    def pending_changes(self) -> list[str]:
        """Return list of files with uncommitted/pending changes."""

    @abc.abstractmethod
    def current_branch(self) -> str:
        """Return the current branch/stream name."""


# ---------------------------------------------------------------------------
# Git backend
# ---------------------------------------------------------------------------

class GitBackend(VcsBackend):
    """Git implementation of VCS queries."""

    def __init__(self, repo_path: str):
        self.repo_path = repo_path

    def _run(self, *args, timeout: int = 10) -> str:
        try:
            result = subprocess.run(
                ["git"] + list(args),
                cwd=self.repo_path,
                capture_output=True, text=True, timeout=timeout,
            )
            return result.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return ""

    def recent_changes(self, since: datetime, limit: int = 50) -> list[VcsChange]:
        since_str = since.strftime("%Y-%m-%dT%H:%M:%S")
        # --format: hash|author|timestamp|subject
        raw = self._run(
            "log", f"--since={since_str}", f"--max-count={limit}",
            "--format=%H|%an|%aI|%s", "--name-only",
        )
        if not raw:
            return []

        changes = []
        current: dict | None = None

        for line in raw.split("\n"):
            if "|" in line and len(line.split("|")) == 4:
                # New commit header
                if current:
                    changes.append(self._make_change(current))
                parts = line.split("|", 3)
                current = {
                    "hash": parts[0],
                    "author": parts[1],
                    "timestamp": parts[2],
                    "message": parts[3],
                    "files": [],
                }
            elif current and line.strip():
                current["files"].append(line.strip())

        if current:
            changes.append(self._make_change(current))

        return changes

    @staticmethod
    def _make_change(data: dict) -> VcsChange:
        try:
            ts = datetime.fromisoformat(data["timestamp"])
        except (ValueError, TypeError):
            ts = datetime.now()
        return VcsChange(
            id=data["hash"][:8],
            author=data["author"],
            message=data["message"],
            timestamp=ts,
            files=data["files"],
        )

    def pending_changes(self) -> list[str]:
        raw = self._run("status", "--porcelain")
        if not raw:
            return []
        files = []
        for line in raw.split("\n"):
            if line.strip():
                # Status is first 2 chars, then space, then path
                path = line[3:].strip()
                if path:
                    files.append(path)
        return files

    def current_branch(self) -> str:
        return self._run("branch", "--show-current") or "unknown"


# ---------------------------------------------------------------------------
# P4V backend stub — implement when ready
# ---------------------------------------------------------------------------

class P4VBackend(VcsBackend):
    """Perforce implementation stub.

    To implement:
      - recent_changes: `p4 changes -s submitted -t //depot/...@since,now`
        then `p4 describe -s <cl>` for each
      - pending_changes: `p4 opened` or `p4 status`
      - current_branch: `p4 client -o` to get stream/workspace
    """

    def __init__(self, port: str = "", client: str = "", user: str = ""):
        self.port = port
        self.client = client
        self.user = user

    def _run(self, *args, timeout: int = 10) -> str:
        cmd = ["p4"]
        if self.port:
            cmd += ["-p", self.port]
        if self.client:
            cmd += ["-c", self.client]
        if self.user:
            cmd += ["-u", self.user]
        cmd += list(args)
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout,
            )
            return result.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return ""

    def recent_changes(self, since: datetime, limit: int = 50) -> list[VcsChange]:
        # TODO: implement with p4 changes + p4 describe
        return []

    def pending_changes(self) -> list[str]:
        # TODO: implement with p4 opened
        return []

    def current_branch(self) -> str:
        # TODO: implement with p4 client -o
        return "unknown"


# ---------------------------------------------------------------------------
# Analysis: map file changes to project themes and cross-ref with digest DB
# ---------------------------------------------------------------------------

# File path patterns → theme hints
FILE_THEME_PATTERNS = {
    r"Vine|Sensor|Signal|Gate|Logic|Turret|Tower|Wave": "Game Systems",
    r"Enemy|Boss|Item|Loot|Drop|Reward|Registry": "Content",
    r"UI|Hud|Menu|Screen|Panel|Button|Theme|Tron|Icon": "Visual Polish",
    r"Balance|Stat|Damage|Health|Cost|Rate|Scaling": "Balance / Tuning",
    r"Manager|Service|Event|Component|Editor|Debug|Tool": "Architecture / Code",
    r"AXIS|Bit|Commentary|Lore|Dialogue|String": "AXIS / Lore",
}


def infer_theme_from_files(files: list[str]) -> dict[str, int]:
    """Count theme signals from file paths."""
    theme_counts: dict[str, int] = {}
    for f in files:
        basename = os.path.basename(f)
        for pattern, theme in FILE_THEME_PATTERNS.items():
            if re.search(pattern, basename, re.IGNORECASE):
                theme_counts[theme] = theme_counts.get(theme, 0) + 1
    return theme_counts


@dataclass
class VcsInsight:
    """An insight generated by analyzing VCS activity against the digest DB."""
    type: str          # "progress", "drift", "stall", "untracked"
    priority: str      # "HIGH", "MEDIUM", "LOW"
    summary: str       # human-readable one-liner
    details: str       # longer explanation
    related_item: str  # matched digest DB item text, if any
    related_tag: str   # matched item's tag
    timestamp: str


class VcsAnalyzer:
    """Analyzes VCS changes against the digest DB to generate insights."""

    def __init__(self, db_path: str = DEFAULT_DB_PATH, verbose: bool = False):
        self.db_path = db_path
        self.verbose = verbose
        self._theme_keywords = load_training()

    def analyze_changes(self, changes: list[VcsChange],
                        pending: list[str]) -> list[VcsInsight]:
        """Analyze recent changes and generate insights."""
        insights = []

        if not changes and not pending:
            return insights

        # 1. Map changes to themes
        all_files = []
        all_messages = []
        for c in changes:
            all_files.extend(c.files)
            all_messages.append(c.message)

        work_themes = infer_theme_from_files(all_files)
        primary_theme = max(work_themes, key=work_themes.get) if work_themes else ""

        # 2. Search digest DB for ACTION items
        db = DigestDB(self.db_path)
        try:
            action_items = db.search_by_tag("ACTION", limit=20)
            decision_items = db.search_by_tag("DECISION", limit=20)

            # 3. Check for progress — commits that match action items
            for action in action_items:
                action_text = action["text"].lower()
                # Extract key nouns from the action item
                action_words = set(re.findall(r'\b\w{4,}\b', action_text))

                for change in changes:
                    msg_words = set(re.findall(r'\b\w{4,}\b', change.message.lower()))
                    file_words = set()
                    for f in change.files:
                        file_words.update(
                            re.findall(r'\b\w{4,}\b', os.path.basename(f).lower())
                        )

                    overlap = action_words & (msg_words | file_words)
                    if len(overlap) >= 2:
                        insights.append(VcsInsight(
                            type="progress",
                            priority="MEDIUM",
                            summary=f"Commit {change.id} likely addresses: {action['text'][:60]}",
                            details=(f"Commit: {change.message}\n"
                                     f"Files: {', '.join(change.files[:5])}\n"
                                     f"Keyword overlap: {', '.join(sorted(overlap))}"),
                            related_item=action["text"],
                            related_tag="ACTION",
                            timestamp=change.timestamp.strftime("%H:%M"),
                        ))

            # 4. Check for drift — work theme doesn't match recent discussion themes
            if primary_theme and action_items:
                discussed_themes = set()
                for item in action_items + decision_items:
                    if item.get("theme"):
                        discussed_themes.add(item["theme"])

                if primary_theme not in discussed_themes and discussed_themes:
                    insights.append(VcsInsight(
                        type="drift",
                        priority="LOW",
                        summary=f"Work is in '{primary_theme}' but recent discussions focused on: {', '.join(discussed_themes)}",
                        details=(f"Files changed map to: {primary_theme}\n"
                                 f"Recent ACTION/DECISION items are about: {', '.join(discussed_themes)}\n"
                                 f"This might be intentional — just flagging the mismatch."),
                        related_item="",
                        related_tag="",
                        timestamp=datetime.now().strftime("%H:%M"),
                    ))

            # 5. Check for untracked action items — discussed but no matching commits
            for action in action_items:
                score = action.get("triage_score", 0)
                if score < 70:  # only flag high-priority items
                    continue

                action_text = action["text"].lower()
                action_words = set(re.findall(r'\b\w{4,}\b', action_text))

                has_matching_commit = False
                for change in changes:
                    msg_words = set(re.findall(r'\b\w{4,}\b', change.message.lower()))
                    if len(action_words & msg_words) >= 2:
                        has_matching_commit = True
                        break

                if not has_matching_commit:
                    insights.append(VcsInsight(
                        type="untracked",
                        priority="HIGH" if score >= 80 else "MEDIUM",
                        summary=f"No commits match action item: {action['text'][:60]}",
                        details=(f"Score: {score}/100 ({action.get('triage_grade', '')})\n"
                                 f"Theme: {action.get('theme', '')}\n"
                                 f"This actionable item has no matching VCS activity."),
                        related_item=action["text"],
                        related_tag="ACTION",
                        timestamp=datetime.now().strftime("%H:%M"),
                    ))

        finally:
            db.close()

        return insights


# ---------------------------------------------------------------------------
# Monitor thread
# ---------------------------------------------------------------------------

class VcsMonitor:
    """Periodically polls VCS and generates insights.

    on_insight(insight: VcsInsight) — called for each significant finding.
    """

    def __init__(self, stop_event: threading.Event,
                 backend: VcsBackend,
                 on_insight=None,
                 poll_interval: float = 120.0,
                 lookback_minutes: int = 60,
                 db_path: str = DEFAULT_DB_PATH,
                 verbose: bool = False):
        self.stop_event = stop_event
        self.backend = backend
        self.on_insight = on_insight
        self.poll_interval = poll_interval
        self.lookback_minutes = lookback_minutes
        self.verbose = verbose

        self._analyzer = VcsAnalyzer(db_path=db_path, verbose=verbose)
        self._seen_change_ids: set[str] = set()
        self._seen_insight_keys: set[str] = set()

        # Stats
        self.changes_seen = 0
        self.insights_generated = 0

    def _poll(self):
        since = datetime.now() - timedelta(minutes=self.lookback_minutes)
        changes = self.backend.recent_changes(since)
        pending = self.backend.pending_changes()

        # Filter out already-seen changes
        new_changes = []
        for c in changes:
            if c.id not in self._seen_change_ids:
                self._seen_change_ids.add(c.id)
                new_changes.append(c)
                self.changes_seen += 1

        if not new_changes and not pending:
            return

        if self.verbose and new_changes:
            print(f"  [vcs] {len(new_changes)} new commits detected")

        insights = self._analyzer.analyze_changes(new_changes, pending)

        for insight in insights:
            # Dedup by summary prefix
            key = insight.summary[:40].lower()
            if key in self._seen_insight_keys:
                continue
            self._seen_insight_keys.add(key)

            self.insights_generated += 1

            if self.verbose:
                print(f"  [vcs] [{insight.priority}] {insight.type}: {insight.summary}")

            if self.on_insight:
                self.on_insight(insight)

        # Cap dedup sets
        if len(self._seen_change_ids) > 500:
            self._seen_change_ids = set(list(self._seen_change_ids)[-250:])
        if len(self._seen_insight_keys) > 200:
            self._seen_insight_keys = set(list(self._seen_insight_keys)[-100:])

    def run(self):
        """Blocking — runs until stop_event is set."""
        branch = self.backend.current_branch()
        if self.verbose:
            print(f"  [vcs] monitoring branch: {branch}, "
                  f"polling every {self.poll_interval}s")

        while not self.stop_event.is_set():
            try:
                self._poll()
            except Exception as e:
                if self.verbose:
                    print(f"  [vcs] poll error: {e}")

            self.stop_event.wait(timeout=self.poll_interval)
