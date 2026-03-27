"""Clipboard Chat Monitor — polls clipboard for chat text during recording.

Every 2 seconds, reads clipboard text. On change, appends it as a [CHAT]
entry to the transcript buffer so it gets included in Claude batches.
"""

import threading
import time
import tkinter as tk
from datetime import datetime


class ChatMonitor:
    """Polls clipboard for new text and appends chat entries to transcript buffer."""

    def __init__(self, stop_event: threading.Event,
                 buffer_lock: threading.Lock, transcript_buffer: list[str],
                 poll_interval: float = 2.0, verbose: bool = False):
        self.stop_event = stop_event
        self.buffer_lock = buffer_lock
        self.transcript_buffer = transcript_buffer
        self.poll_interval = poll_interval
        self.verbose = verbose

        self._last_text = ""
        self._tk: tk.Tk | None = None

    def _get_clipboard(self) -> str:
        """Read clipboard text. Returns empty string on failure."""
        try:
            if self._tk is None:
                self._tk = tk.Tk()
                self._tk.withdraw()
            text = self._tk.clipboard_get()
            return text if isinstance(text, str) else ""
        except (tk.TclError, Exception):
            return ""

    @staticmethod
    def _is_chat_like(text: str) -> bool:
        """Simple heuristic: short text, no binary, likely from chat."""
        if not text or len(text) > 2000:
            return False
        # Skip if it looks like binary or code blocks
        if '\x00' in text:
            return False
        # Must have at least some words
        words = text.split()
        return 2 <= len(words) <= 200

    def run(self):
        """Blocking — runs until stop_event is set."""
        # Initialize with current clipboard to avoid capturing stale text
        self._last_text = self._get_clipboard()
        if self.verbose:
            print("  [chat] clipboard monitor started")

        while not self.stop_event.is_set():
            self.stop_event.wait(timeout=self.poll_interval)
            if self.stop_event.is_set():
                break

            text = self._get_clipboard()
            if text and text != self._last_text:
                self._last_text = text
                if self._is_chat_like(text):
                    timestamp = datetime.now().strftime("%H:%M")
                    # Collapse whitespace
                    clean = " ".join(text.split())
                    line = f"[{timestamp}] [CHAT] {clean}"
                    with self.buffer_lock:
                        self.transcript_buffer.append(line)
                    if self.verbose:
                        preview = clean[:80] + ("..." if len(clean) > 80 else "")
                        print(f"  [chat] captured: {preview}")

        # Cleanup tkinter
        if self._tk:
            try:
                self._tk.destroy()
            except Exception:
                pass
