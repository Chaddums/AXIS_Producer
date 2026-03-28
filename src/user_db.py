"""User feedback + term database — SQLite store for scoring signals.

Two systems in one DB (axis_user.db):

1. Feedback: records dismiss/resolve/follow/backlog actions from the dashboard,
   derives per-term weight signals from accumulated feedback history.

2. Terms: user-controlled dictionary of terms with explicit weights that
   boost or suppress items during triage scoring.
"""

import os
import re
import sqlite3
from collections import defaultdict
from datetime import datetime

DEFAULT_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "axis_user.db")

# Stop words filtered during term extraction
_STOP_WORDS = frozenset({
    "a", "an", "the", "is", "it", "in", "on", "at", "to", "of", "for",
    "and", "or", "but", "not", "with", "from", "by", "as", "was", "were",
    "be", "been", "has", "had", "have", "do", "does", "did", "this", "that",
    "they", "them", "their", "its", "are", "can", "will", "just", "about",
    "also", "into", "than", "then", "some", "what", "when", "where", "who",
    "how", "all", "each", "every", "both", "few", "more", "most", "other",
    "some", "such", "only", "own", "same", "so", "very", "too", "out",
    "up", "down", "over", "under", "again", "once", "here", "there",
    "why", "way", "still", "should", "would", "could", "may", "might",
})

# Feedback action weights — how much each action signals about the terms in that item
_ACTION_WEIGHTS = {
    "follow": 5,
    "backlog": 3,
    "resolve": 1,
    "dismiss": -5,
}

_DERIVED_CAP = 50  # max absolute value for feedback-derived term weights


def extract_terms(text: str) -> list[str]:
    """Extract meaningful terms from text. Lowercase, no punctuation, no stop words."""
    words = re.findall(r'[a-z0-9]+', text.lower())
    return [w for w in words if len(w) >= 3 and w not in _STOP_WORDS]


class UserDB:
    """SQLite store for feedback signals and user-defined terms."""

    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = db_path
        self._conn = None

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._ensure_tables()
        return self._conn

    def _ensure_tables(self):
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL DEFAULT (datetime('now')),
                action TEXT NOT NULL,
                item_text TEXT NOT NULL,
                item_tag TEXT NOT NULL DEFAULT '',
                item_theme TEXT NOT NULL DEFAULT ''
            );

            CREATE INDEX IF NOT EXISTS idx_feedback_action ON feedback(action);

            CREATE TABLE IF NOT EXISTS terms (
                term TEXT PRIMARY KEY,
                weight INTEGER NOT NULL DEFAULT 0,
                theme TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '',
                created TEXT NOT NULL DEFAULT (datetime('now')),
                updated TEXT NOT NULL DEFAULT (datetime('now'))
            );
        """)
        self._conn.commit()

    # ------------------------------------------------------------------
    # Feedback
    # ------------------------------------------------------------------

    def record_feedback(self, action: str, text: str, tag: str = "", theme: str = ""):
        """Record a user feedback action on an item."""
        if action not in _ACTION_WEIGHTS:
            return
        conn = self._connect()
        conn.execute(
            "INSERT INTO feedback (action, item_text, item_tag, item_theme) VALUES (?, ?, ?, ?)",
            (action, text, tag, theme),
        )
        conn.commit()

    def get_feedback_stats(self) -> dict:
        """Summary counts by action."""
        conn = self._connect()
        rows = conn.execute(
            "SELECT action, COUNT(*) as count FROM feedback GROUP BY action"
        ).fetchall()
        return {r["action"]: r["count"] for r in rows}

    def get_term_signals(self) -> dict[str, float]:
        """Aggregate feedback into per-term weights.

        Extracts terms from every feedback item's text, applies action weights,
        sums per term, caps at [-50, +50].
        """
        conn = self._connect()
        rows = conn.execute("SELECT action, item_text FROM feedback").fetchall()

        term_scores = defaultdict(float)
        for row in rows:
            weight = _ACTION_WEIGHTS.get(row["action"], 0)
            terms = extract_terms(row["item_text"])
            for term in terms:
                term_scores[term] += weight

        # Cap values
        return {
            term: max(-_DERIVED_CAP, min(_DERIVED_CAP, score))
            for term, score in term_scores.items()
            if abs(score) >= 1  # skip near-zero noise
        }

    # ------------------------------------------------------------------
    # Terms (user-controlled)
    # ------------------------------------------------------------------

    def set_term(self, term: str, weight: int, theme: str = "", notes: str = ""):
        """Add or update a user-defined term."""
        term = term.strip().lower()
        if not term:
            return
        weight = max(-100, min(100, weight))
        conn = self._connect()
        conn.execute("""
            INSERT INTO terms (term, weight, theme, notes, updated)
            VALUES (?, ?, ?, ?, datetime('now'))
            ON CONFLICT(term) DO UPDATE SET
                weight = excluded.weight,
                theme = excluded.theme,
                notes = excluded.notes,
                updated = datetime('now')
        """, (term, weight, theme, notes))
        conn.commit()

    def get_term(self, term: str) -> dict | None:
        """Get a single term's data."""
        conn = self._connect()
        row = conn.execute("SELECT * FROM terms WHERE term = ?", (term.lower(),)).fetchone()
        return dict(row) if row else None

    def get_all_terms(self) -> list[dict]:
        """Get all user-defined terms, sorted by absolute weight descending."""
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM terms ORDER BY ABS(weight) DESC, term ASC"
        ).fetchall()
        return [dict(r) for r in rows]

    def delete_term(self, term: str) -> bool:
        """Remove a user-defined term. Returns True if it existed."""
        conn = self._connect()
        cursor = conn.execute("DELETE FROM terms WHERE term = ?", (term.lower(),))
        conn.commit()
        return cursor.rowcount > 0

    def import_terms(self, terms_list: list[dict]) -> int:
        """Bulk upsert terms. Returns count imported."""
        count = 0
        for t in terms_list:
            term = t.get("term", "").strip().lower()
            if not term:
                continue
            self.set_term(
                term,
                int(t.get("weight", 0)),
                t.get("theme", ""),
                t.get("notes", ""),
            )
            count += 1
        return count

    # ------------------------------------------------------------------
    # Combined: effective weights for triage
    # ------------------------------------------------------------------

    def get_effective_weights(self) -> dict[str, dict]:
        """Merge feedback-derived signals with explicit user terms.

        Explicit terms always win over feedback-derived signals.

        Returns: {term: {weight, theme, source}}
            source is "explicit" or "feedback"
        """
        # Start with feedback signals
        signals = self.get_term_signals()
        result = {
            term: {"weight": score, "theme": "", "source": "feedback"}
            for term, score in signals.items()
        }

        # Overlay explicit terms (these win)
        for row in self.get_all_terms():
            result[row["term"]] = {
                "weight": row["weight"],
                "theme": row["theme"],
                "source": "explicit",
            }

        return result

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None
