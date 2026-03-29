"""Clipboard Chat Monitor — polls clipboard for chat text during recording.

Every 2 seconds, reads clipboard text. On change, appends it as a [CHAT]
entry to the transcript buffer so it gets included in Claude batches.
"""

import ctypes
import threading
import time
from datetime import datetime


def _get_clipboard_text() -> str:
    """Read clipboard text via Win32 API. Returns empty string on failure."""
    try:
        import subprocess
        result = subprocess.run(
            ["powershell", "-Command", "Get-Clipboard"],
            capture_output=True, text=True, timeout=3,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except Exception:
        return ""


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
        self._last_text = _get_clipboard_text()
        if self.verbose:
            print("  [chat] clipboard monitor started")

        while not self.stop_event.is_set():
            self.stop_event.wait(timeout=self.poll_interval)
            if self.stop_event.is_set():
                break

            text = _get_clipboard_text()
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
