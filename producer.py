"""Batch Processor — periodically sends transcript buffer to Claude and appends structured notes to log."""

import os
import threading
import time
from datetime import datetime

import anthropic

BATCH_INTERVAL_SEC = 300
MIN_WORDS_TO_BATCH = 50
MODEL = "claude-sonnet-4-20250514"
MAX_TOKENS = 1024

SYSTEM_PROMPT = """\
You are a producer observing a working session between collaborators.
Your job is to extract signal from their conversation and produce structured notes.
The conversation may cover any topic — development, design, strategy, philosophy, \
business, technology, or anything else. Capture ALL substantive discussion.

For each batch of transcript you receive, output ONLY the following sections \
(omit any section that has nothing to report):

## Decisions Locked
- [specific decisions made, stated as facts]

## Ideas Generated
- [new concepts, insights, arguments, or approaches discussed]

## Open Questions
- [unresolved questions raised, phrased as questions]

## Action Items
- [specific tasks someone said they would do]

## Watch List
- [concerns, risks, or disagreements flagged]

## Blockers
- [anything someone said they're blocked on, waiting for, or can't proceed without]
- [format: WHO is blocked on WHAT — e.g. "Stu blocked on art assets from Adam"]

## Key Discussion
- [important points, arguments, or observations that don't fit the above categories]

Rules:
- Be terse. One line per item.
- No editorializing. Capture what was said, not your opinion of it.
- If someone says "we should" or "we need to" — that's an action item.
- If something was discussed but not resolved — that's an open question.
- If someone says "blocked", "waiting on", "can't do X until Y", "stuck on" — that's a blocker.
- Blockers are NOT the same as action items. A blocker means someone CANNOT proceed.
- Ignore only filler and repetition, NOT topic changes. If they're talking about it, log it.
- If the transcript is genuinely unintelligible, output: [nothing to report]"""

AI_DISCLAIMER = "\n\n> *AI-generated summary. Not a verbatim transcript. Not a legal record. Verify important decisions independently.*"


class BatchProducer:
    """Periodically batches the transcript buffer, sends to Claude,
    and appends structured notes to the session log file."""

    def __init__(self, stop_event: threading.Event,
                 buffer_lock: threading.Lock, transcript_buffer: list[str],
                 log_path: str, interval: int = BATCH_INTERVAL_SEC,
                 verbose: bool = False, on_items_logged=None,
                 workspace_context: str = "",
                 output_terminology: dict | None = None):
        self.stop_event = stop_event
        self.buffer_lock = buffer_lock
        self.transcript_buffer = transcript_buffer
        self.log_path = log_path
        self.interval = interval
        self.verbose = verbose
        self.on_items_logged = on_items_logged  # callback(list[tuple[str, str]]) — (category, text)
        self.output_terminology = output_terminology or {}

        # Build system prompt with optional workspace context
        self._system_prompt = SYSTEM_PROMPT
        if workspace_context:
            self._system_prompt += f"\n\nContext: {workspace_context}"

        self.force_batch = threading.Event()
        self._batch_count = 0
        self._client = anthropic.Anthropic()  # uses ANTHROPIC_API_KEY env var

    def run(self):
        """Blocking — runs until stop_event is set."""
        self._write_header()

        while not self.stop_event.is_set():
            # Wait for interval or force trigger
            triggered = self.force_batch.wait(timeout=self.interval)
            if triggered:
                self.force_batch.clear()

            if self.stop_event.is_set():
                break

            self._process_batch()

        # Final flush on shutdown
        self._process_batch()

    def startup_check(self):
        """Run a mini batch with no word minimum to verify the pipeline works."""
        with self.buffer_lock:
            if not self.transcript_buffer:
                return False
            transcript = "\n".join(self.transcript_buffer)
            self.transcript_buffer.clear()

        word_count = len(transcript.split())
        if word_count == 0:
            return False

        self._batch_count += 1
        timestamp = datetime.now().strftime("%H:%M")

        if self.verbose:
            print(f"  [startup] {word_count} words captured -> Claude")

        try:
            response = self._client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=self._system_prompt,
                messages=[{"role": "user", "content": transcript}],
            )
            notes = self._apply_terminology(response.content[0].text)
        except Exception as e:
            print(f"  [startup] Claude API error: {e}")
            with self.buffer_lock:
                self.transcript_buffer.insert(0, transcript)
            return False

        self._append_to_log(timestamp, notes)
        print(f"  [startup] pipeline OK -- batch logged to session log")

        if self.on_items_logged:
            items = self._extract_items(notes)
            if items:
                try:
                    self.on_items_logged(items)
                except Exception:
                    pass

        return True

    def _process_batch(self):
        with self.buffer_lock:
            if not self.transcript_buffer:
                return
            transcript = "\n".join(self.transcript_buffer)
            self.transcript_buffer.clear()

        word_count = len(transcript.split())
        if word_count < MIN_WORDS_TO_BATCH:
            if self.verbose:
                print(f"  [producer] only {word_count} words, below threshold -- keeping in buffer")
            # Put it back
            with self.buffer_lock:
                self.transcript_buffer.insert(0, transcript)
            return

        self._batch_count += 1
        timestamp = datetime.now().strftime("%H:%M")

        if self.verbose:
            print(f"  [producer] batch {self._batch_count} -- {word_count} words -> Claude")

        try:
            response = self._client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=self._system_prompt,
                messages=[{"role": "user", "content": transcript}],
            )
            notes = self._apply_terminology(response.content[0].text)
        except Exception as e:
            print(f"  [producer] Claude API error: {e}")
            # Keep the transcript for next batch
            with self.buffer_lock:
                self.transcript_buffer.insert(0, transcript)
            return

        self._append_to_log(timestamp, notes)

        if self.verbose:
            print(f"  [producer] batch {self._batch_count} logged")

        # Extract items and fire callback for taskbar notifications
        if self.on_items_logged:
            items = self._extract_items(notes)
            if items:
                try:
                    self.on_items_logged(items)
                except Exception as e:
                    if self.verbose:
                        print(f"  [producer] callback error: {e}")

    @staticmethod
    def _extract_items(notes: str) -> list[tuple[str, str]]:
        """Parse Claude's structured notes into (category, text) tuples."""
        items = []
        current_category = None
        for line in notes.split("\n"):
            line = line.strip()
            if line.startswith("## "):
                current_category = line[3:].strip()
            elif line.startswith("- ") and current_category:
                items.append((current_category, line[2:].strip()))
        return items

    def _apply_terminology(self, notes: str) -> str:
        """Remap section headers if output_terminology overrides are set."""
        for original, replacement in self.output_terminology.items():
            notes = notes.replace(f"## {original}", f"## {replacement}")
        return notes

    def _write_header(self):
        started = datetime.now().strftime("%Y-%m-%d %H:%M")
        header = f"# AXIS Session Log\nStarted: {started}\n\n---\n\n"
        with open(self.log_path, "w", encoding="utf-8") as f:
            f.write(header)

    def _append_to_log(self, timestamp: str, notes: str):
        entry = f"## [{timestamp}] Batch {self._batch_count}\n\n{notes}{AI_DISCLAIMER}\n\n---\n\n"
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(entry)
