"""Cloud Sync — pushes local events to Supabase and subscribes to remote events.

Follows the monitor pattern: __init__(...), blocking run().
Other monitors call push_event() to enqueue events for upload.
"""

import os
import queue
import threading
import time
import logging
from datetime import datetime, timezone

import anthropic

from cloud_db import CloudDB
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
    """Pushes local events to Supabase and subscribes to remote events.

    Thread-safe: other monitors call push_event() from any thread.
    """

    def __init__(self, stop_event: threading.Event,
                 on_remote_event=None,
                 on_synthesis=None,
                 supabase_url: str = "",
                 supabase_key: str = "",
                 user_identity: str = "stu",
                 synthesis_interval: int = 900,
                 project: str | None = None,
                 verbose: bool = False):
        self.stop_event = stop_event
        self.on_remote_event = on_remote_event    # callback(dict)
        self.on_synthesis = on_synthesis            # callback(str)
        self._url = supabase_url
        self._key = supabase_key
        self._user = user_identity
        self._synthesis_interval = synthesis_interval
        self._project = project
        self.verbose = verbose

        self._outbound: queue.Queue = queue.Queue()
        self._db: CloudDB | None = None
        self._last_synthesis: float = 0.0
        self._anthropic: anthropic.Anthropic | None = None

    # --- Public: enqueue events (thread-safe) ---

    def push_event(self, event: dict):
        """Enqueue a raw event dict for upload. Non-blocking."""
        self._outbound.put(event)

    def push_claude_event(self, ce: ClaudeEvent):
        """Convert a ClaudeEvent to the cloud schema and enqueue."""
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
                         session_id: str = ""):
        """Convert BatchProducer items [(category, text), ...] to cloud events."""
        for category, text in items:
            self.push_event({
                "ts": datetime.now(timezone.utc).isoformat(),
                "who": self._user,
                "stream": "voice",
                "session_id": session_id,
                "event_type": category.lower().replace(" ", "_"),
                "area": None,
                "files": [],
                "summary": text,
                "raw": {"category": category},
                "project": self._project,
            })

    def push_git_event(self, event_type: str, summary: str,
                       files: list[str] | None = None,
                       raw: dict | None = None):
        """Push a git/VCS event."""
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
        """Push a chat/slack/email message event."""
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
        self._db = CloudDB(self._url, self._key, verbose=self.verbose)

        if not self._db.connected:
            if self.verbose:
                print("  [sync] Supabase not connected — running in local-only mode")
            # Still drain the queue so push_event() doesn't block callers
            while not self.stop_event.is_set():
                self._drain_queue_discard()
                self.stop_event.wait(timeout=10.0)
            return

        # Track last-seen event ID for polling remote events
        self._last_event_id = 0
        # Seed: get current max ID so we only see new events going forward
        existing = self._db.query_events(limit=1)
        if existing:
            self._last_event_id = existing[0].get("id", 0)

        if self.verbose:
            print(f"  [sync] cloud sync active as '{self._user}' "
                  f"(synthesis every {self._synthesis_interval}s)")

        # Init synthesis timer
        self._last_synthesis = time.time()

        while not self.stop_event.is_set():
            self._drain_queue()
            self._poll_remote()
            self._maybe_synthesize()
            self.stop_event.wait(timeout=5.0)

        # Flush remaining events
        self._drain_queue()
        self._db = None

    # --- Internal ---

    def _drain_queue(self):
        """Batch-drain up to 50 events from the outbound queue."""
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

    def _drain_queue_discard(self):
        """Drain and discard events when not connected."""
        while True:
            try:
                self._outbound.get_nowait()
            except queue.Empty:
                break

    def _poll_remote(self):
        """Poll Supabase for new events from other users."""
        if not self._db:
            return
        events, max_id = self._db.poll_new_events(
            since_id=self._last_event_id,
            who_exclude=self._user,
        )
        self._last_event_id = max_id
        for event in events:
            self._handle_remote(event)

    def _handle_remote(self, event: dict):
        """Called when another user's event arrives via realtime."""
        if self.verbose:
            who = event.get("who", "?")
            summary = event.get("summary", "")[:60]
            print(f"  [sync] remote: {who} — {summary}")

        if self.on_remote_event:
            try:
                self.on_remote_event(event)
            except Exception as e:
                log.warning(f"Remote event callback error: {e}")

    def _maybe_synthesize(self):
        """Run synthesis if enough time has elapsed."""
        now = time.time()
        if now - self._last_synthesis < self._synthesis_interval:
            return
        self._last_synthesis = now

        if not self._db:
            return

        # Fetch recent events (window slightly larger than interval)
        minutes = (self._synthesis_interval // 60) + 5
        events = self._db.recent_events(minutes=minutes, project=self._project)

        if len(events) < 3:
            return

        try:
            summary = self._synthesize(events)
        except Exception as e:
            log.warning(f"Synthesis failed: {e}")
            return

        if not summary or summary.strip() == "[not enough activity]":
            return

        # Store synthesis
        timestamps = [e.get("ts", "") for e in events if e.get("ts")]
        window_start = min(timestamps) if timestamps else ""
        window_end = max(timestamps) if timestamps else ""

        self._db.insert_synthesis(
            content=summary,
            window_start=window_start,
            window_end=window_end,
            project=self._project,
        )

        if self.verbose:
            print(f"  [sync] synthesis generated ({len(events)} events)")

        if self.on_synthesis:
            try:
                self.on_synthesis(summary)
            except Exception as e:
                log.warning(f"Synthesis callback error: {e}")

    def _synthesize(self, events: list[dict]) -> str:
        """Call Claude to produce a cross-stream activity summary."""
        if not self._anthropic:
            api_key = os.environ.get("ANTHROPIC_API_KEY", "")
            if not api_key:
                return ""
            self._anthropic = anthropic.Anthropic()

        # Format events for the prompt
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

        resp = self._anthropic.messages.create(
            model=SYNTHESIS_MODEL,
            max_tokens=SYNTHESIS_MAX_TOKENS,
            system=SYNTHESIS_PROMPT,
            messages=[{
                "role": "user",
                "content": f"Here are the recent events:\n\n{event_text}",
            }],
        )

        return resp.content[0].text if resp.content else ""

    @staticmethod
    def _infer_area(files: list[str]) -> str | None:
        """Infer a project area from file paths (best-effort)."""
        if not files:
            return None

        # Use the most common directory component across files
        components = []
        for f in files:
            parts = f.replace("\\", "/").split("/")
            # Skip drive letters and common prefixes
            meaningful = [p for p in parts
                          if p and p not in ("C:", "Users", "GitHub", "src",
                                             "Scripts", "Scenes", "Resources")]
            if len(meaningful) >= 2:
                # Take the second-to-last directory as the area
                components.append(meaningful[-2])

        if not components:
            return None

        # Return most common
        from collections import Counter
        most_common = Counter(components).most_common(1)
        return most_common[0][0] if most_common else None
