"""Calendar Monitor — polls Outlook calendar for upcoming and active meetings.

Uses win32com to read the Outlook calendar. Detects:
- Upcoming meetings (fires pre-meeting callback with time-to-start)
- Meeting start/end (fires lifecycle callbacks)
- Deadlines on the calendar that affect triage priority

Falls back gracefully if Outlook isn't running or win32com isn't installed.
"""

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta


def _outlook_available() -> bool:
    try:
        import win32com.client
        return True
    except ImportError:
        return False


@dataclass
class CalendarEvent:
    """A calendar event (meeting, deadline, reminder)."""
    subject: str
    start: datetime
    end: datetime
    location: str = ""
    organizer: str = ""
    body_preview: str = ""
    entry_id: str = ""
    is_recurring: bool = False

    @property
    def duration_minutes(self) -> int:
        return int((self.end - self.start).total_seconds() / 60)

    @property
    def minutes_until_start(self) -> float:
        return (self.start - datetime.now()).total_seconds() / 60

    @property
    def is_active(self) -> bool:
        now = datetime.now()
        return self.start <= now <= self.end

    @property
    def is_past(self) -> bool:
        return datetime.now() > self.end

    def __repr__(self):
        return f"CalendarEvent({self.subject!r}, {self.start:%H:%M}-{self.end:%H:%M})"


class CalendarMonitor:
    """Polls Outlook calendar and fires callbacks for meeting lifecycle events.

    Callbacks:
        on_meeting_approaching(event, minutes_until) — 10 min before start
        on_meeting_started(event) — when a meeting's start time is reached
        on_meeting_ended(event) — when a meeting's end time is reached
        on_events_updated(upcoming: list[CalendarEvent]) — full list refresh
    """

    def __init__(self, stop_event: threading.Event,
                 on_meeting_approaching=None,
                 on_meeting_started=None,
                 on_meeting_ended=None,
                 on_events_updated=None,
                 poll_interval: float = 60.0,
                 lookahead_hours: int = 24,
                 pre_meeting_minutes: int = 10,
                 verbose: bool = False):
        self.stop_event = stop_event
        self.on_meeting_approaching = on_meeting_approaching
        self.on_meeting_started = on_meeting_started
        self.on_meeting_ended = on_meeting_ended
        self.on_events_updated = on_events_updated
        self.poll_interval = poll_interval
        self.lookahead_hours = lookahead_hours
        self.pre_meeting_minutes = pre_meeting_minutes
        self.verbose = verbose

        self._outlook = None
        self._calendar = None

        # Track lifecycle state per event to avoid duplicate callbacks
        self._approached: set[str] = set()   # entry_ids we've fired approaching for
        self._started: set[str] = set()      # entry_ids we've fired started for
        self._ended: set[str] = set()        # entry_ids we've fired ended for

        # Current event list (accessible by other components)
        self.upcoming_events: list[CalendarEvent] = []
        self.active_event: CalendarEvent | None = None

    def _connect_outlook(self) -> bool:
        try:
            import win32com.client
            import pythoncom
            pythoncom.CoInitialize()
            self._outlook = win32com.client.Dispatch("Outlook.Application")
            namespace = self._outlook.GetNamespace("MAPI")
            # 9 = olFolderCalendar
            self._calendar = namespace.GetDefaultFolder(9)
            if self.verbose:
                print("  [calendar] connected to Outlook calendar")
            return True
        except Exception as e:
            if self.verbose:
                print(f"  [calendar] Outlook connect failed: {e}")
            return False

    def _com_datetime(self, com_dt) -> datetime:
        """Convert COM datetime to Python datetime."""
        try:
            return datetime(com_dt.year, com_dt.month, com_dt.day,
                            com_dt.hour, com_dt.minute, com_dt.second)
        except Exception:
            return datetime.now()

    def _fetch_events(self) -> list[CalendarEvent]:
        """Fetch upcoming events from Outlook calendar."""
        if self._calendar is None:
            return []

        events = []
        now = datetime.now()
        end_window = now + timedelta(hours=self.lookahead_hours)

        try:
            items = self._calendar.Items
            items.IncludeRecurrences = True
            items.Sort("[Start]")

            # Filter to our time window
            filter_str = (
                f"[Start] >= '{now.strftime('%m/%d/%Y %H:%M %p')}' AND "
                f"[Start] <= '{end_window.strftime('%m/%d/%Y %H:%M %p')}'"
            )
            restricted = items.Restrict(filter_str)

            for i in range(min(restricted.Count, 30)):
                try:
                    item = restricted.Item(i + 1)

                    # Only appointment items (class 26)
                    if item.Class != 26:
                        continue

                    start = self._com_datetime(item.Start)
                    end = self._com_datetime(item.End)

                    # Skip all-day events (they're usually not meetings)
                    if item.AllDayEvent:
                        continue

                    body = str(item.Body or "")[:300].strip()

                    events.append(CalendarEvent(
                        subject=str(item.Subject or "(no subject)"),
                        start=start,
                        end=end,
                        location=str(item.Location or ""),
                        organizer=str(item.Organizer or ""),
                        body_preview=body,
                        entry_id=str(item.EntryID or f"cal_{i}"),
                        is_recurring=bool(item.IsRecurring),
                    ))
                except Exception as e:
                    if self.verbose:
                        print(f"  [calendar] error reading event: {e}")

        except Exception as e:
            if self.verbose:
                print(f"  [calendar] fetch error: {e}")

        return events

    def _check_lifecycle(self, events: list[CalendarEvent]):
        """Check each event for lifecycle transitions and fire callbacks."""
        now = datetime.now()
        self.active_event = None

        for event in events:
            eid = event.entry_id
            mins_until = event.minutes_until_start

            # Active meeting detection
            if event.is_active:
                self.active_event = event

            # Approaching: within pre_meeting_minutes of start, not yet started
            if (0 < mins_until <= self.pre_meeting_minutes
                    and eid not in self._approached):
                self._approached.add(eid)
                if self.verbose:
                    print(f"  [calendar] meeting in {mins_until:.0f}m: {event.subject}")
                if self.on_meeting_approaching:
                    self.on_meeting_approaching(event, mins_until)

            # Started: start time passed, not yet fired
            if mins_until <= 0 and not event.is_past and eid not in self._started:
                self._started.add(eid)
                if self.verbose:
                    print(f"  [calendar] meeting started: {event.subject}")
                if self.on_meeting_started:
                    self.on_meeting_started(event)

            # Ended: end time passed, we had marked it as started
            if event.is_past and eid in self._started and eid not in self._ended:
                self._ended.add(eid)
                if self.verbose:
                    print(f"  [calendar] meeting ended: {event.subject}")
                if self.on_meeting_ended:
                    self.on_meeting_ended(event)

        # Cleanup old tracking entries (events from yesterday etc)
        for tracking_set in (self._approached, self._started, self._ended):
            if len(tracking_set) > 100:
                # Can't easily prune by time, just cap size
                excess = list(tracking_set)[:-50]
                for eid in excess:
                    tracking_set.discard(eid)

    def _poll(self):
        events = self._fetch_events()
        self.upcoming_events = events
        self._check_lifecycle(events)

        if self.on_events_updated:
            self.on_events_updated(events)

    def run(self):
        """Blocking — runs until stop_event is set."""
        if not _outlook_available():
            if self.verbose:
                print("  [calendar] win32com not installed — calendar monitor disabled")
            return

        if not self._connect_outlook():
            return

        if self.verbose:
            print(f"  [calendar] monitoring, polling every {self.poll_interval}s, "
                  f"lookahead {self.lookahead_hours}h")

        # Initial poll
        self._poll()
        if self.verbose and self.upcoming_events:
            print(f"  [calendar] {len(self.upcoming_events)} upcoming events:")
            for ev in self.upcoming_events[:5]:
                print(f"    {ev.start:%H:%M} — {ev.subject}")

        while not self.stop_event.is_set():
            self.stop_event.wait(timeout=self.poll_interval)
            if self.stop_event.is_set():
                break
            self._poll()

        # COM cleanup
        try:
            import pythoncom
            pythoncom.CoUninitialize()
        except Exception:
            pass

    # ----- Query methods (called by other components) -----

    def next_event(self) -> CalendarEvent | None:
        """Return the next upcoming event, or None."""
        now = datetime.now()
        for ev in self.upcoming_events:
            if ev.start > now:
                return ev
        return None

    def events_in_range(self, hours: float) -> list[CalendarEvent]:
        """Return events starting within the next N hours."""
        cutoff = datetime.now() + timedelta(hours=hours)
        return [ev for ev in self.upcoming_events
                if datetime.now() < ev.start <= cutoff]

    def has_deadline_today(self, keywords: list[str] = None) -> list[CalendarEvent]:
        """Find events that look like deadlines (keyword match in subject)."""
        if keywords is None:
            keywords = ["deadline", "due", "release", "ship", "playtest",
                        "review", "demo", "milestone", "freeze", "cutoff"]

        today_end = datetime.now().replace(hour=23, minute=59, second=59)
        deadlines = []
        for ev in self.upcoming_events:
            if ev.start <= today_end:
                subject_lower = ev.subject.lower()
                if any(kw in subject_lower for kw in keywords):
                    deadlines.append(ev)
        return deadlines
