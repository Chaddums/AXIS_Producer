"""Cloud Sync — pushes local events to backend and subscribes to remote events.

Follows the monitor pattern: __init__(...), blocking run().
Other monitors call push_event() to enqueue events for upload.

When a BackendClient is available, all traffic goes through the backend API.
Falls back to direct Supabase (via CloudDB) if no backend is configured,
for backward compatibility during migration.
"""

import os
import queue
import threading
import time
import logging
from datetime import datetime, timezone

from claude_monitor import ClaudeEvent

log = logging.getLogger(__name__)

SYNTHESIS_MODEL = "claude-sonnet-4-20250514"
SYNTHESIS_MAX_TOKENS = 2048

SYNTHESIS_PROMPT = """\
You are analyzing a shared activity log for a small development team.
The log contains events from multiple sources: Claude Code conversations,
voice transcripts, git commits, chat messages, and more.

For each person, summarize:
1. What they're currently working on (files, systems, features)
2. Key decisions made or questions raised
3. Any blockers or things they're waiting on

Then provide:
- **Potential Conflicts**: files or systems being touched by multiple people
- **Cross-cutting Concerns**: topics discussed in voice that relate to code being written
- **Blockers & Dependencies**: who is waiting on what from whom

Be terse. One line per item. No editorializing.
If there's not enough activity to synthesize, output: [not enough activity]"""


class CloudSync:
    """Pushes local events to backend and subscribes to remote events.

    Thread-safe: other monitors call push_event() from any thread.
    """

    def __init__(self, stop_event: threading.Event,
                 on_remote_event=None,
                 on_synthesis=None,
                 backend_client=None,
                 team_id: str = "",
                 # Legacy params — used only if backend_client is None
                 supabase_url: str = "",
                 supabase_key: str = "",
                 user_identity: str = "stu",
                 synthesis_interval: int = 900,
                 project: str | None = None,
                 verbose: bool = False):
        self.stop_event = stop_event
        self.on_remote_event = on_remote_event
        self.on_synthesis = on_synthesis
        self._backend = backend_client
        self._team_id = team_id
        self._url = supabase_url
        self._key = supabase_key
        self._user = user_identity
        self._synthesis_interval = synthesis_interval
        self._project = project
        self.verbose = verbose

        self._outbound: queue.Queue = queue.Queue()
        self._db = None  # CloudDB, only used in legacy mode
        self._last_synthesis: float = 0.0
        self._last_event_id = "0"
        self._private_mode: bool = False

    @property
    def _use_backend(self) -> bool:
        return self._backend is not None and self._team_id

    # --- Private mode ---

    @property
    def private_mode(self) -> bool:
        return self._private_mode

    @private_mode.setter
    def private_mode(self, value: bool):
        self._private_mode = value
        if self.verbose:
            state = "ON (events stay local)" if value else "OFF (syncing)"
            print(f"  [sync] private mode: {state}")

    # --- Content gate ---

    @staticmethod
    def _is_shareable(event: dict) -> bool:
        summary = (event.get("summary") or "").lower()
        stream = event.get("stream", "")
        event_type = event.get("event_type", "")

        if event_type in ("file_edit", "file_read", "write", "bash_command",
                          "search", "commit", "branch_status", "unpushed",
                          "unpulled", "divergence"):
            return True
        if stream == "presence":
            return True

        skip_patterns = [
            "complain", "venting", "rant", "frustrated", "annoyed",
            "pissed", "hate this", "so tired", "burned out",
            "drama", "gossip", "personal",
            "what are you up to", "how's it going", "lol", "lmao",
            "haha", "brb", "gtg",
        ]
        for pattern in skip_patterns:
            if pattern in summary:
                return False

        if len(summary) < 10 and event_type in ("user_message", "chat"):
            action_words = ["done", "merged", "pushed", "pulled", "fixed",
                           "shipped", "deployed", "yes", "no", "approved"]
            if not any(w in summary for w in action_words):
                return False

        return True

    # --- Public: enqueue events (thread-safe) ---

    def push_event(self, event: dict):
        if self._private_mode:
            return
        if not self._is_shareable(event):
            if self.verbose:
                print(f"  [sync] filtered (not shareable): {event.get('summary', '')[:50]}")
            return
        # Add team_id for backend mode
        if self._use_backend and "team_id" not in event:
            event["team_id"] = self._team_id
        self._outbound.put(event)

    def push_claude_event(self, ce: ClaudeEvent):
        self.push_event({
            "ts": ce.timestamp or datetime.now(timezone.utc).isoformat(),
            "who": self._user,
            "stream": "claude_code",
            "session_id": ce.session_id,
            "event_type": ce.event_type,
            "area": self._infer_area(ce.files),
            "files": ce.files,
            "summary": ce.summary,
            "raw": {"tool": ce.tool_name, "branch": ce.branch,
                    "project": ce.project},
            "project": ce.project or self._project,
        })

    def push_voice_batch(self, items: list[tuple[str, str]],
                         session_id: str = "", notes: str = ""):
        """Push one summary event per batch with items nested inside."""
        if not items:
            return
        # Summary: most actionable items as the headline, not category counts
        priority_order = ["Blockers", "Action Items", "Decisions Locked", "Watch List",
                          "Open Questions", "Ideas Generated", "Key Discussion"]
        headline_items = []
        for cat in priority_order:
            for c, text in items:
                if c == cat and len(headline_items) < 3:
                    headline_items.append(text)
        summary_line = " | ".join(headline_items) if headline_items else items[0][1]

        CATEGORY_PRIORITY = {
            "Blockers": "critical", "Action Items": "warning",
            "Watch List": "warning", "Decisions Locked": "success",
            "Ideas Generated": "info", "Open Questions": "info",
            "Key Discussion": "ambient",
        }
        PRIORITY_RANK = {"critical": 4, "warning": 3, "success": 2, "info": 1, "ambient": 0}
        priorities = [CATEGORY_PRIORITY.get(cat, "info") for cat, _ in items]
        top_priority = max(priorities, key=lambda p: PRIORITY_RANK.get(p, 0))

        self.push_event({
            "ts": datetime.now(timezone.utc).isoformat(),
            "who": self._user,
            "stream": "voice",
            "session_id": session_id,
            "event_type": "session_batch",
            "area": None,
            "files": [],
            "summary": summary_line,
            "raw": {
                "priority": top_priority,
                "items": [{"category": cat, "text": text} for cat, text in items],
                "notes": notes,
            },
            "project": self._project,
        })

    def push_git_event(self, event_type: str, summary: str,
                       files: list[str] | None = None,
                       raw: dict | None = None):
        self.push_event({
            "ts": datetime.now(timezone.utc).isoformat(),
            "who": self._user,
            "stream": "git",
            "session_id": "",
            "event_type": event_type,
            "area": self._infer_area(files or []),
            "files": files or [],
            "summary": summary,
            "raw": raw or {},
            "project": self._project,
        })

    def push_message_event(self, source: str, sender: str,
                           text: str, timestamp: str = ""):
        self.push_event({
            "ts": timestamp or datetime.now(timezone.utc).isoformat(),
            "who": self._user,
            "stream": source.split(":")[0] if ":" in source else source,
            "session_id": "",
            "event_type": "message",
            "area": None,
            "files": [],
            "summary": f"{sender}: {text[:200]}",
            "raw": {"source": source, "sender": sender},
            "project": self._project,
        })

    # --- Main loop ---

    def run(self):
        """Blocking — runs until stop_event is set."""
        if self._use_backend:
            self._run_backend()
        else:
            self._run_legacy()

    def _run_backend(self):
        """Run using backend API for all cloud operations."""
        if not self._backend.connected:
            if self.verbose:
                print("  [sync] Backend unreachable — running in local-only mode")
            while not self.stop_event.is_set():
                self._drain_queue_discard()
                self.stop_event.wait(timeout=10.0)
            return

        if self.verbose:
            print(f"  [sync] cloud sync active via backend as '{self._user}' "
                  f"(team {self._team_id[:8]}...)")

        self._last_synthesis = time.time()

        while not self.stop_event.is_set():
            self._drain_queue_backend()
            self._poll_remote_backend()
            self._maybe_synthesize_backend()
            self.stop_event.wait(timeout=5.0)

        self._drain_queue_backend()

    def _run_legacy(self):
        """Run using direct Supabase access (backward compat)."""
        from cloud_db import CloudDB
        self._db = CloudDB(self._url, self._key, verbose=self.verbose)

        if not self._db.connected:
            if self.verbose:
                print("  [sync] Supabase not connected — running in local-only mode")
            while not self.stop_event.is_set():
                self._drain_queue_discard()
                self.stop_event.wait(timeout=10.0)
            return

        self._last_event_id = 0
        existing = self._db.query_events(limit=1)
        if existing:
            self._last_event_id = existing[0].get("id", 0)

        if self.verbose:
            print(f"  [sync] cloud sync active (legacy/Supabase) as '{self._user}'")

        self._last_synthesis = time.time()

        while not self.stop_event.is_set():
            self._drain_queue_legacy()
            self._poll_remote_legacy()
            self._maybe_synthesize_legacy()
            self.stop_event.wait(timeout=5.0)

        self._drain_queue_legacy()
        self._db = None

    # --- Backend mode internals ---

    def _drain_queue_backend(self):
        batch = []
        while len(batch) < 50:
            try:
                batch.append(self._outbound.get_nowait())
            except queue.Empty:
                break
        if batch:
            count = self._backend.push_events(batch)
            if self.verbose and count:
                print(f"  [sync] pushed {count} events via backend")

    def _poll_remote_backend(self):
        events = self._backend.poll_events(
            self._team_id, since_id=self._last_event_id, limit=50
        )
        for event in events:
            eid = event.get("id", self._last_event_id)
            if str(eid) > str(self._last_event_id):
                self._last_event_id = str(eid)
            if event.get("who") != self._user:
                self._handle_remote(event)

    def _maybe_synthesize_backend(self):
        """Local synthesis — group events by person/theme, no LLM call needed."""
        now = time.time()
        if now - self._last_synthesis < self._synthesis_interval:
            return
        self._last_synthesis = now

        events = self._backend.poll_events(self._team_id, limit=200)
        if len(events) < 3:
            return

        summary_text = self._local_synthesis(events)
        if not summary_text:
            return

        timestamps = [e.get("ts", "") for e in events if e.get("ts")]
        window_start = min(timestamps) if timestamps else ""
        window_end = max(timestamps) if timestamps else ""

        self._backend.push_synthesis(
            self._team_id, summary_text, window_start, window_end
        )

        if self.verbose:
            print(f"  [sync] synthesis generated via backend ({len(events)} events)")

        if self.on_synthesis:
            try:
                self.on_synthesis(summary_text)
            except Exception as e:
                log.warning(f"Synthesis callback error: {e}")

    # --- Legacy mode internals ---

    def _drain_queue_legacy(self):
        batch = []
        while len(batch) < 50:
            try:
                batch.append(self._outbound.get_nowait())
            except queue.Empty:
                break
        if batch and self._db:
            count = self._db.insert_events(batch)
            if self.verbose and count:
                print(f"  [sync] pushed {count} events to cloud")

    def _poll_remote_legacy(self):
        if not self._db:
            return
        events, max_id = self._db.poll_new_events(
            since_id=self._last_event_id,
            who_exclude=self._user,
        )
        self._last_event_id = max_id
        for event in events:
            self._handle_remote(event)

    @staticmethod
    def _local_synthesis(events: list[dict]) -> str:
        """Synthesize events locally — group by person and stream, no LLM needed."""
        from collections import defaultdict, Counter

        by_person = defaultdict(list)
        streams = Counter()
        for e in events:
            who = e.get("who", "?")
            if e.get("stream") == "presence" or e.get("event_type") == "presence":
                continue
            summary = e.get("summary", "")
            if not summary:
                continue
            by_person[who].append(summary[:100])
            streams[e.get("stream", "?")] += 1

        if not by_person:
            return ""

        lines = [f"Team activity ({len(events)} events, {len(by_person)} people):"]
        lines.append(f"Sources: {', '.join(f'{s}({c})' for s, c in streams.most_common(5))}")
        lines.append("")

        for who, summaries in sorted(by_person.items(), key=lambda x: -len(x[1])):
            lines.append(f"**{who}** ({len(summaries)} items):")
            for s in summaries[:3]:
                lines.append(f"  - {s}")
            if len(summaries) > 3:
                lines.append(f"  ... and {len(summaries) - 3} more")
            lines.append("")

        return "\n".join(lines)

    def _maybe_synthesize_legacy(self):
        now = time.time()
        if now - self._last_synthesis < self._synthesis_interval:
            return
        self._last_synthesis = now

        if not self._db:
            return

        minutes = (self._synthesis_interval // 60) + 5
        events = self._db.recent_events(minutes=minutes, project=self._project)

        if len(events) < 3:
            return

        try:
            summary = self._local_synthesis(events)
        except Exception as e:
            log.warning(f"Synthesis failed: {e}")
            return

        if not summary or summary.strip() == "[not enough activity]":
            return

        timestamps = [e.get("ts", "") for e in events if e.get("ts")]
        window_start = min(timestamps) if timestamps else ""
        window_end = max(timestamps) if timestamps else ""

        self._db.insert_synthesis(
            content=summary, window_start=window_start,
            window_end=window_end, project=self._project,
        )

        if self.verbose:
            print(f"  [sync] synthesis generated ({len(events)} events)")

        if self.on_synthesis:
            try:
                self.on_synthesis(summary)
            except Exception as e:
                log.warning(f"Synthesis callback error: {e}")

    def _synthesize_legacy(self, events: list[dict]) -> str:
        import anthropic as _anthropic
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            return ""
        client = _anthropic.Anthropic()

        lines = []
        for e in events:
            ts = e.get("ts", "?")
            who = e.get("who", "?")
            stream = e.get("stream", "?")
            summary = e.get("summary", "")
            files = e.get("files", [])
            files_str = f" [{', '.join(files)}]" if files else ""
            lines.append(f"[{ts}] {who} ({stream}): {summary}{files_str}")

        event_text = "\n".join(lines)

        resp = client.messages.create(
            model=SYNTHESIS_MODEL,
            max_tokens=SYNTHESIS_MAX_TOKENS,
            system=SYNTHESIS_PROMPT,
            messages=[{"role": "user", "content": f"Here are the recent events:\n\n{event_text}"}],
        )

        return resp.content[0].text if resp.content else ""

    # --- Shared ---

    def _drain_queue_discard(self):
        while True:
            try:
                self._outbound.get_nowait()
            except queue.Empty:
                break

    def _handle_remote(self, event: dict):
        if self.verbose:
            who = event.get("who", "?")
            summary = event.get("summary", "")[:60]
            print(f"  [sync] remote: {who} — {summary}")

        if self.on_remote_event:
            try:
                self.on_remote_event(event)
            except Exception as e:
                log.warning(f"Remote event callback error: {e}")

    @staticmethod
    def _infer_area(files: list[str]) -> str | None:
        if not files:
            return None
        components = []
        for f in files:
            parts = f.replace("\\", "/").split("/")
            meaningful = [p for p in parts
                          if p and p not in ("C:", "Users", "GitHub", "src",
                                             "Scripts", "Scenes", "Resources")]
            if len(meaningful) >= 2:
                components.append(meaningful[-2])
        if not components:
            return None
        from collections import Counter
        most_common = Counter(components).most_common(1)
        return most_common[0][0] if most_common else None
