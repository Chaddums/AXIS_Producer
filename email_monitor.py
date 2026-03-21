"""Email Monitor — reads recent Outlook emails via COM on Windows.

Uses win32com to connect to the running Outlook instance and poll the
Inbox for unread or recent emails. Pushes each new email to the focus
advisor for priority matching.

Falls back gracefully if Outlook isn't running or win32com isn't installed.
"""

import threading
import time
from datetime import datetime, timedelta


def _outlook_available() -> bool:
    """Check if win32com and Outlook COM are available."""
    try:
        import win32com.client
        return True
    except ImportError:
        return False


class EmailMonitor:
    """Polls Outlook Inbox for new/unread emails and forwards to a callback.

    callback(source: str, sender: str, text: str, timestamp: str)
    """

    def __init__(self, stop_event: threading.Event,
                 on_message=None,
                 poll_interval: float = 30.0,
                 max_age_minutes: int = 60,
                 unread_only: bool = True,
                 verbose: bool = False):
        self.stop_event = stop_event
        self.on_message = on_message
        self.poll_interval = poll_interval
        self.max_age_minutes = max_age_minutes
        self.unread_only = unread_only
        self.verbose = verbose

        self._seen_ids: set[str] = set()
        self._outlook = None
        self._inbox = None

    def _connect_outlook(self) -> bool:
        """Connect to running Outlook instance via COM."""
        try:
            import win32com.client
            import pythoncom
            pythoncom.CoInitialize()
            self._outlook = win32com.client.Dispatch("Outlook.Application")
            namespace = self._outlook.GetNamespace("MAPI")
            # 6 = olFolderInbox
            self._inbox = namespace.GetDefaultFolder(6)
            if self.verbose:
                print(f"  [email] connected to Outlook -- "
                      f"{self._inbox.Items.Count} inbox items")
            return True
        except Exception as e:
            if self.verbose:
                print(f"  [email] Outlook connect failed: {e}")
            return False

    def _poll_inbox(self):
        """Check for new emails since last poll."""
        if self._inbox is None:
            return

        try:
            items = self._inbox.Items
            items.Sort("[ReceivedTime]", True)  # newest first

            cutoff = datetime.now() - timedelta(minutes=self.max_age_minutes)
            count = 0

            for i in range(min(items.Count, 50)):  # check last 50
                try:
                    mail = items.Item(i + 1)  # COM is 1-indexed
                except Exception:
                    continue

                # Check if it's a mail item (not meeting invite etc)
                # MailItem class = 43
                try:
                    if mail.Class != 43:
                        continue
                except Exception:
                    continue

                # Check age
                try:
                    received = mail.ReceivedTime
                    # COM datetime → Python datetime
                    recv_dt = datetime(received.year, received.month, received.day,
                                      received.hour, received.minute, received.second)
                    if recv_dt < cutoff:
                        break  # sorted newest-first, so stop here
                except Exception:
                    continue

                # Check unread filter
                if self.unread_only and mail.UnRead is False:
                    continue

                # Deduplicate by EntryID
                try:
                    entry_id = mail.EntryID
                    if entry_id in self._seen_ids:
                        continue
                    self._seen_ids.add(entry_id)
                except Exception:
                    continue

                # Extract fields
                try:
                    sender = str(mail.SenderName or mail.SenderEmailAddress or "unknown")
                    subject = str(mail.Subject or "(no subject)")
                    body = str(mail.Body or "")
                    # Truncate body for focus matching — just first 500 chars
                    body_preview = body[:500].strip()
                    ts = recv_dt.strftime("%H:%M")

                    text = f"{subject}\n{body_preview}" if body_preview else subject

                    if self.verbose:
                        print(f"  [email] {ts} from {sender}: {subject[:60]}")

                    if self.on_message:
                        self.on_message(
                            source="email:outlook",
                            sender=sender,
                            text=text,
                            timestamp=ts,
                        )
                    count += 1
                except Exception as e:
                    if self.verbose:
                        print(f"  [email] error reading mail: {e}")

            if count > 0 and self.verbose:
                print(f"  [email] processed {count} new emails")

        except Exception as e:
            if self.verbose:
                print(f"  [email] poll error: {e}")

    def run(self):
        """Blocking — runs until stop_event is set."""
        if not _outlook_available():
            if self.verbose:
                print("  [email] win32com not installed -- email monitor disabled")
                print("  [email] install with: pip install pywin32")
            return

        if not self._connect_outlook():
            return

        # Pre-seed seen IDs with current inbox to avoid initial flood
        try:
            items = self._inbox.Items
            items.Sort("[ReceivedTime]", True)
            for i in range(min(items.Count, 50)):
                try:
                    mail = items.Item(i + 1)
                    self._seen_ids.add(mail.EntryID)
                except Exception:
                    pass
        except Exception:
            pass

        if self.verbose:
            print(f"  [email] monitoring inbox every {self.poll_interval}s")

        while not self.stop_event.is_set():
            self.stop_event.wait(timeout=self.poll_interval)
            if self.stop_event.is_set():
                break
            self._poll_inbox()

        # COM cleanup
        try:
            import pythoncom
            pythoncom.CoUninitialize()
        except Exception:
            pass

        # Cap seen_ids to prevent unbounded growth
        if len(self._seen_ids) > 1000:
            # Keep only the most recent (arbitrary trim — no ordering, but fine)
            self._seen_ids = set(list(self._seen_ids)[-500:])
