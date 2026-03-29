"""SQLite + FTS5 search index for session digest items.

Stores every digest item with its tag, theme, timestamp, session date,
and triage score/grade. Provides full-text keyword search via FTS5.

DB location: tools/axis_producer/digest.db (next to this file)
"""

import os
import sqlite3

DEFAULT_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "digest.db")


class DigestDB:
    """SQLite + FTS5 index for session digest items."""

    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = db_path
        self._conn = None

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path)
            self._conn.row_factory = sqlite3.Row
            self._ensure_tables()
        return self._conn

    def _ensure_tables(self):
        conn = self._conn
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_date TEXT NOT NULL,
                batch_time TEXT NOT NULL,
                tag TEXT NOT NULL,
                theme TEXT NOT NULL DEFAULT '',
                text TEXT NOT NULL,
                triage_score INTEGER NOT NULL DEFAULT 0,
                triage_grade TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(
                tag, theme, text,
                content='items',
                content_rowid='id'
            );

            CREATE TRIGGER IF NOT EXISTS items_ai AFTER INSERT ON items BEGIN
                INSERT INTO items_fts(rowid, tag, theme, text)
                VALUES (new.id, new.tag, new.theme, new.text);
            END;

            CREATE TRIGGER IF NOT EXISTS items_ad AFTER DELETE ON items BEGIN
                INSERT INTO items_fts(items_fts, rowid, tag, theme, text)
                VALUES ('delete', old.id, old.tag, old.theme, old.text);
            END;

            CREATE TRIGGER IF NOT EXISTS items_au AFTER UPDATE ON items BEGIN
                INSERT INTO items_fts(items_fts, rowid, tag, theme, text)
                VALUES ('delete', old.id, old.tag, old.theme, old.text);
                INSERT INTO items_fts(rowid, tag, theme, text)
                VALUES (new.id, new.tag, new.theme, new.text);
            END;
        """)
        # Add columns to existing DBs (idempotent)
        for col, typedef in [
            ("triage_score", "INTEGER NOT NULL DEFAULT 0"),
            ("triage_grade", "TEXT NOT NULL DEFAULT ''"),
            ("content_hash", "TEXT NOT NULL DEFAULT ''"),
            ("term_vector", "TEXT NOT NULL DEFAULT '[]'"),
            ("session_id", "TEXT NOT NULL DEFAULT ''"),
        ]:
            try:
                conn.execute(f"SELECT {col} FROM items LIMIT 1")
            except sqlite3.OperationalError:
                conn.execute(f"ALTER TABLE items ADD COLUMN {col} {typedef}")

        # Session summaries table for trend tracking
        conn.execute("""
            CREATE TABLE IF NOT EXISTS session_summaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_date TEXT NOT NULL,
                duration_minutes INTEGER DEFAULT 0,
                total_items INTEGER DEFAULT 0,
                items_by_grade TEXT DEFAULT '{}',
                items_by_theme TEXT DEFAULT '{}',
                top_items TEXT DEFAULT '[]',
                llm_tokens_used INTEGER DEFAULT 0,
                report_path TEXT DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)

        # Index on content_hash for fast exact dedup
        try:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_items_hash ON items(content_hash)")
        except sqlite3.OperationalError:
            pass
        conn.commit()

    def insert_item(self, session_date: str, batch_time: str,
                    tag: str, theme: str, text: str,
                    triage_score: int = 0, triage_grade: str = ""):
        conn = self._connect()
        conn.execute(
            "INSERT INTO items (session_date, batch_time, tag, theme, text, triage_score, triage_grade) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (session_date, batch_time, tag, theme, text, triage_score, triage_grade),
        )
        conn.commit()

    def insert_items(self, items: list[dict]):
        """Bulk insert. Each dict: session_date, batch_time, tag, theme, text,
        and optionally triage_score, triage_grade."""
        conn = self._connect()
        conn.executemany(
            "INSERT INTO items (session_date, batch_time, tag, theme, text, triage_score, triage_grade) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(i["session_date"], i["batch_time"], i["tag"], i["theme"], i["text"],
              i.get("triage_score", 0), i.get("triage_grade", "")) for i in items],
        )
        conn.commit()

    def search(self, query: str, limit: int = 20) -> list[dict]:
        """Full-text search across tag, theme, and text fields.

        Automatically adds prefix matching (sensor -> sensor*) so partial
        words match plurals and suffixed forms.
        """
        conn = self._connect()
        terms = query.strip().split()
        fts_query = " ".join(f"{t}*" for t in terms if t)
        if not fts_query:
            return []
        rows = conn.execute("""
            SELECT i.session_date, i.batch_time, i.tag, i.theme, i.text,
                   i.triage_score, i.triage_grade, rank
            FROM items_fts
            JOIN items i ON i.id = items_fts.rowid
            WHERE items_fts MATCH ?
            ORDER BY rank
            LIMIT ?
        """, (fts_query, limit)).fetchall()
        return [dict(r) for r in rows]

    def search_by_tag(self, tag: str, limit: int = 50) -> list[dict]:
        """Filter by tag (DECISION, IDEA, ACTION, QUESTION, WATCH)."""
        conn = self._connect()
        rows = conn.execute(
            "SELECT session_date, batch_time, tag, theme, text, triage_score, triage_grade "
            "FROM items WHERE tag = ? ORDER BY id DESC LIMIT ?",
            (tag.upper(), limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def search_by_theme(self, theme: str, limit: int = 50) -> list[dict]:
        """Filter by theme category (partial match)."""
        conn = self._connect()
        rows = conn.execute(
            "SELECT session_date, batch_time, tag, theme, text, triage_score, triage_grade "
            "FROM items WHERE theme LIKE ? ORDER BY id DESC LIMIT ?",
            (f"%{theme}%", limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def search_by_grade(self, grade: str, limit: int = 50) -> list[dict]:
        """Filter by triage grade (actionable, needs-context, parked, stale)."""
        conn = self._connect()
        rows = conn.execute(
            "SELECT session_date, batch_time, tag, theme, text, triage_score, triage_grade "
            "FROM items WHERE triage_grade = ? ORDER BY triage_score DESC LIMIT ?",
            (grade.lower(), limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def recent(self, limit: int = 20) -> list[dict]:
        """Get most recent items."""
        conn = self._connect()
        rows = conn.execute(
            "SELECT session_date, batch_time, tag, theme, text, triage_score, triage_grade "
            "FROM items ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def stats(self) -> dict:
        """Return counts by tag, theme, and grade."""
        conn = self._connect()
        total = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        by_tag = conn.execute(
            "SELECT tag, COUNT(*) as count FROM items GROUP BY tag ORDER BY count DESC"
        ).fetchall()
        by_theme = conn.execute(
            "SELECT theme, COUNT(*) as count FROM items GROUP BY theme ORDER BY count DESC"
        ).fetchall()
        by_grade = conn.execute(
            "SELECT triage_grade, COUNT(*) as count FROM items WHERE triage_grade != '' "
            "GROUP BY triage_grade ORDER BY count DESC"
        ).fetchall()
        sessions = conn.execute(
            "SELECT DISTINCT session_date FROM items ORDER BY session_date DESC"
        ).fetchall()
        avg_score = conn.execute(
            "SELECT AVG(triage_score) FROM items WHERE triage_score > 0"
        ).fetchone()[0]
        return {
            "total": total,
            "by_tag": {r["tag"]: r["count"] for r in by_tag},
            "by_theme": {r["theme"]: r["count"] for r in by_theme},
            "by_grade": {r["triage_grade"]: r["count"] for r in by_grade},
            "sessions": [r["session_date"] for r in sessions],
            "avg_triage_score": round(avg_score, 1) if avg_score else 0,
        }

    def find_by_hash(self, h: str) -> dict | None:
        """Find an item by content hash (exact dedup)."""
        conn = self._connect()
        row = conn.execute(
            "SELECT * FROM items WHERE content_hash = ? LIMIT 1", (h,)
        ).fetchone()
        return dict(row) if row else None

    def get_items_since(self, date: str, limit: int = 200) -> list[dict]:
        """Get items since a date for trend comparisons."""
        conn = self._connect()
        rows = conn.execute(
            "SELECT session_date, batch_time, tag, theme, text, triage_score, triage_grade "
            "FROM items WHERE session_date >= ? ORDER BY id DESC LIMIT ?",
            (date, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def insert_session_summary(self, session_date: str, duration_minutes: int = 0,
                               total_items: int = 0, items_by_grade: str = "{}",
                               items_by_theme: str = "{}", top_items: str = "[]",
                               llm_tokens_used: int = 0, report_path: str = ""):
        """Record a session summary for trend tracking."""
        conn = self._connect()
        conn.execute(
            "INSERT INTO session_summaries "
            "(session_date, duration_minutes, total_items, items_by_grade, "
            " items_by_theme, top_items, llm_tokens_used, report_path) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (session_date, duration_minutes, total_items, items_by_grade,
             items_by_theme, top_items, llm_tokens_used, report_path),
        )
        conn.commit()

    def get_session_summaries(self, limit: int = 20) -> list[dict]:
        """Get recent session summaries for trend analysis."""
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM session_summaries ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None
