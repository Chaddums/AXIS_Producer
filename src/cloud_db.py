"""Cloud DB — Supabase client wrapper for the shared event store.

Handles inserting events, querying, real-time subscriptions, and synthesis
records. All methods are network-failure-safe (log + return None/empty).

Requires: supabase>=2.0.0
    pip install supabase
"""

import logging
import time
from datetime import datetime, timedelta, timezone

try:
    from supabase import create_client, Client
    _HAS_SUPABASE = True
except ImportError:
    _HAS_SUPABASE = False
    Client = None

log = logging.getLogger(__name__)


class CloudDB:
    """Wrapper around Supabase for the shared AXIS event store."""

    def __init__(self, url: str, key: str, verbose: bool = False):
        self.url = url
        self.key = key
        self.verbose = verbose
        self._client: Client | None = None
        self._channels: list = []

        if not _HAS_SUPABASE:
            log.warning("supabase package not installed — cloud sync disabled")
            return
        if not url or not key:
            log.warning("supabase_url or supabase_key not set — cloud sync disabled")
            return

        try:
            self._client = create_client(url, key)
            if self.verbose:
                print("  [cloud] connected to Supabase")
        except Exception as e:
            log.warning(f"Supabase connection failed: {e}")
            self._client = None

    @property
    def connected(self) -> bool:
        return self._client is not None

    # --- Write ---

    def insert_event(self, event: dict) -> dict | None:
        """Insert a single event row. Returns the inserted row or None on error.

        Expected keys: ts, who, stream, session_id, event_type, area, files,
                       summary, raw, project
        """
        if not self._client:
            return None
        try:
            resp = self._client.table("events").insert(event).execute()
            if resp.data:
                return resp.data[0]
        except Exception as e:
            if self.verbose:
                print(f"  [cloud] insert error: {e}")
            log.warning(f"Event insert failed: {e}")
        return None

    def insert_events(self, events: list[dict]) -> int:
        """Bulk insert. Returns count of successfully inserted rows."""
        if not self._client or not events:
            return 0
        try:
            resp = self._client.table("events").insert(events).execute()
            return len(resp.data) if resp.data else 0
        except Exception as e:
            if self.verbose:
                print(f"  [cloud] bulk insert error: {e}")
            log.warning(f"Bulk insert failed: {e}")
            # Fall back to one-at-a-time
            count = 0
            for event in events:
                if self.insert_event(event):
                    count += 1
            return count

    def insert_synthesis(self, content: str, window_start: str,
                         window_end: str, project: str | None = None) -> dict | None:
        """Insert a synthesis record."""
        if not self._client:
            return None
        try:
            row = {
                "content": content,
                "window_start": window_start,
                "window_end": window_end,
            }
            if project:
                row["project"] = project
            resp = self._client.table("syntheses").insert(row).execute()
            if resp.data:
                return resp.data[0]
        except Exception as e:
            if self.verbose:
                print(f"  [cloud] synthesis insert error: {e}")
            log.warning(f"Synthesis insert failed: {e}")
        return None

    # --- Read ---

    def query_events(self,
                     who: str | None = None,
                     stream: str | None = None,
                     project: str | None = None,
                     event_type: str | None = None,
                     since: str | None = None,
                     until: str | None = None,
                     limit: int = 100) -> list[dict]:
        """Query events with optional filters. Returns list of row dicts."""
        if not self._client:
            return []
        try:
            q = self._client.table("events").select("*")
            if who:
                q = q.eq("who", who)
            if stream:
                q = q.eq("stream", stream)
            if project:
                q = q.eq("project", project)
            if event_type:
                q = q.eq("event_type", event_type)
            if since:
                q = q.gte("ts", since)
            if until:
                q = q.lte("ts", until)
            q = q.order("ts", desc=True).limit(limit)
            resp = q.execute()
            return resp.data or []
        except Exception as e:
            if self.verbose:
                print(f"  [cloud] query error: {e}")
            log.warning(f"Event query failed: {e}")
            return []

    def recent_events(self, minutes: int = 30,
                      project: str | None = None) -> list[dict]:
        """Convenience: events from the last N minutes."""
        since = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
        return self.query_events(since=since, project=project, limit=200)

    def latest_synthesis(self, project: str | None = None) -> dict | None:
        """Get the most recent synthesis."""
        if not self._client:
            return None
        try:
            q = self._client.table("syntheses").select("*")
            if project:
                q = q.eq("project", project)
            q = q.order("ts", desc=True).limit(1)
            resp = q.execute()
            return resp.data[0] if resp.data else None
        except Exception as e:
            log.warning(f"Synthesis query failed: {e}")
            return None

    # --- Remote event polling (replaces realtime for sync client) ---

    def poll_new_events(self, since_id: int = 0,
                        who_exclude: str | None = None,
                        limit: int = 50) -> tuple[list[dict], int]:
        """Poll for events newer than since_id.

        Returns (events, max_id_seen). Pass max_id_seen back as since_id
        on the next call to get only new events.
        """
        if not self._client:
            return [], since_id
        try:
            q = (self._client.table("events")
                 .select("*")
                 .gt("id", since_id)
                 .order("id", desc=False)
                 .limit(limit))
            if who_exclude:
                q = q.neq("who", who_exclude)
            resp = q.execute()
            rows = resp.data or []
            max_id = max((r.get("id", 0) for r in rows), default=since_id)
            return rows, max_id
        except Exception as e:
            if self.verbose:
                print(f"  [cloud] poll error: {e}")
            log.warning(f"Event poll failed: {e}")
            return [], since_id

    # --- Lifecycle ---

    def close(self):
        """Release resources."""
        self._client = None
