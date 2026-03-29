"""Daily Briefing — scheduled outbound communication from AXIS Producer.

Generates contextual nudges at natural moments throughout the day:
- Morning standup (login / configurable time)
- Midday check-in (stale action nag + blocker status)
- End-of-day wrap-up (what happened today)
- Weekly status report (Friday afternoon)

All surface through the same tray popup system — small, dismissible,
copy-to-clipboard. User's choice to engage or dismiss.
"""

import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta

import anthropic

from blocker_tracker import BlockerDB
from calendar_monitor import CalendarEvent
from deadline_scorer import get_deadline_priorities
from digest_db import DigestDB, DEFAULT_DB_PATH

MODEL = "claude-sonnet-4-20250514"
MAX_TOKENS = 1024


# ---------------------------------------------------------------------------
# Briefing types
# ---------------------------------------------------------------------------

@dataclass
class Briefing:
    """A generated briefing ready for display."""
    type: str         # "standup", "checkin", "wrapup", "weekly", "nag"
    title: str
    body: str         # markdown content
    timestamp: str
    dismissable: bool = True
    priority: str = "normal"   # "normal", "high"

    @property
    def display_title(self) -> str:
        titles = {
            "standup": "Good morning",
            "checkin": "Midday check-in",
            "wrapup": "End of day",
            "weekly": "Week in Review",
            "nag": "Friendly reminder",
        }
        return titles.get(self.type, self.title)


# ---------------------------------------------------------------------------
# Generators — each builds a briefing from available data
# ---------------------------------------------------------------------------

def _get_vcs_summary(repo_path: str, since_hours: int = 24) -> str:
    """Get recent VCS activity summary."""
    try:
        from vcs_monitor import GitBackend
        backend = GitBackend(repo_path)
        since = datetime.now() - timedelta(hours=since_hours)
        changes = backend.recent_changes(since, limit=20)
        if not changes:
            return "No commits in the last 24h."
        lines = [f"  {c.id} — {c.author}: {c.message[:60]}" for c in changes[:8]]
        return f"{len(changes)} commits:\n" + "\n".join(lines)
    except Exception:
        return ""


def generate_standup(calendar_events: list[CalendarEvent] = None,
                      repo_path: str = "",
                      db_path: str = DEFAULT_DB_PATH,
                      verbose: bool = False) -> Briefing:
    """Morning standup: yesterday's progress, today's plan, blockers."""
    sections = []

    # Yesterday's digest items — filtered by triage grade (actionable + needs-context only)
    db = DigestDB(db_path)
    try:
        recent = db.recent(limit=50)
        yesterday = datetime.now() - timedelta(days=1)
        yesterday_items = [r for r in recent
                           if r.get("session_date", "")[:10] >= yesterday.strftime("%Y-%m-%d")
                           and r.get("triage_grade", "") in ("actionable", "needs-context")]

        if yesterday_items:
            decisions = [r for r in yesterday_items if r["tag"] == "DECISION"]
            actions = [r for r in yesterday_items if r["tag"] == "ACTION"]
            blockers_items = [r for r in yesterday_items if r["tag"] == "BLOCKER"]
            if decisions:
                sections.append("**Yesterday — Decisions:**")
                for d in sorted(decisions, key=lambda x: -x.get("triage_score", 0))[:5]:
                    sections.append(f"- {d['text']}")
            if actions:
                sections.append("**Yesterday — Actions committed:**")
                for a in sorted(actions, key=lambda x: -x.get("triage_score", 0))[:5]:
                    sections.append(f"- {a['text']}")
            if blockers_items:
                sections.append("**Yesterday — Blockers raised:**")
                for b in blockers_items[:3]:
                    sections.append(f"- {b['text']}")
        else:
            sections.append("*No significant session notes from yesterday.*")

        # Session trend
        summaries = db.get_session_summaries(limit=5)
        if len(summaries) >= 2:
            latest = summaries[0]
            prev = summaries[1]
            latest_total = latest.get("total_items", 0)
            prev_total = prev.get("total_items", 0)
            if latest_total > 0 and prev_total > 0:
                change = latest_total - prev_total
                direction = "up" if change > 0 else "down" if change < 0 else "flat"
                sections.append(f"\n*Trend: {latest_total} items last session ({direction} from {prev_total})*")
    finally:
        db.close()

    # VCS activity
    if repo_path:
        vcs = _get_vcs_summary(repo_path, since_hours=24)
        if vcs:
            sections.append(f"\n**Code activity (24h):**\n{vcs}")

    # Open blockers
    bdb = BlockerDB(db_path)
    try:
        blockers = bdb.get_open_blockers()
        if blockers:
            sections.append(f"\n**Blockers ({len(blockers)} open):**")
            for b in blockers[:3]:
                sev = " CRITICAL" if b.severity == "critical" else ""
                sections.append(f"- {b.text[:70]}{sev}")
    finally:
        bdb.close()

    # Today's calendar
    if calendar_events:
        today_events = [e for e in calendar_events
                        if e.start.date() == datetime.now().date()]
        if today_events:
            sections.append("\n**Today's meetings:**")
            for ev in today_events[:5]:
                sections.append(f"- {ev.start:%H:%M} — {ev.subject}")

    # Deadline priorities
    if calendar_events:
        priorities = get_deadline_priorities(calendar_events, db_path=db_path, limit=3)
        if priorities:
            sections.append("\n**Top priorities (deadline-adjusted):**")
            for p in priorities:
                sections.append(f"- [{p['triage_score']}/100] {p['text'][:60]}")

    body = "\n".join(sections) if sections else "Nothing on the radar. Clear day."

    return Briefing(
        type="standup",
        title="Morning Standup",
        body=body,
        timestamp=datetime.now().strftime("%H:%M"),
    )


def generate_checkin(repo_path: str = "",
                      db_path: str = DEFAULT_DB_PATH,
                      verbose: bool = False) -> Briefing:
    """Midday check-in: stale actions, blocker updates, drift detection."""
    sections = []

    # Stale action items (7+ days, no VCS match)
    db = DigestDB(db_path)
    try:
        actions = db.search_by_tag("ACTION", limit=30)
        stale = []
        for a in actions:
            try:
                session_date = a.get("session_date", "")[:10]
                age = (datetime.now() - datetime.strptime(session_date, "%Y-%m-%d")).days
                if age >= 3:
                    stale.append((age, a))
            except (ValueError, TypeError):
                pass

        stale.sort(key=lambda x: -x[0])

        if stale:
            sections.append(f"**Stale action items ({len(stale)}):**")
            for age, a in stale[:5]:
                score = a.get("triage_score", 0)
                sections.append(f"- [{age}d old, {score}/100] {a['text'][:60]}")
            if len(stale) > 5:
                sections.append(f"  ...and {len(stale) - 5} more")
    finally:
        db.close()

    # Blocker status
    bdb = BlockerDB(db_path)
    try:
        blockers = bdb.get_open_blockers()
        escalated = [b for b in blockers if b.is_escalated]
        if escalated:
            sections.append(f"\n**Escalated blockers ({len(escalated)}):**")
            for b in escalated[:3]:
                sections.append(f"- {b.text[:60]} (priority {b.priority_score}/100, "
                                f"{b.age_days}d, {b.mentions}x mentioned)")
    finally:
        bdb.close()

    # Today's VCS activity so far
    if repo_path:
        vcs = _get_vcs_summary(repo_path, since_hours=6)
        if vcs:
            sections.append(f"\n**Today's commits:**\n{vcs}")

    if not sections:
        return Briefing(
            type="checkin",
            title="Midday Check-in",
            body="All clear — no stale items, no escalated blockers. Keep building.",
            timestamp=datetime.now().strftime("%H:%M"),
        )

    return Briefing(
        type="checkin",
        title="Midday Check-in",
        body="\n".join(sections),
        timestamp=datetime.now().strftime("%H:%M"),
        priority="high" if any(b.severity == "critical"
                                for b in (bdb.get_open_blockers()
                                          if bdb._conn else [])) else "normal",
    )


def generate_wrapup(repo_path: str = "",
                      db_path: str = DEFAULT_DB_PATH,
                      verbose: bool = False) -> Briefing:
    """End-of-day wrap: what happened today, what's still open."""
    sections = []
    today = datetime.now().strftime("%Y-%m-%d")

    db = DigestDB(db_path)
    try:
        recent = db.recent(limit=50)
        today_items = [r for r in recent if r.get("session_date", "")[:10] == today]

        # Filter to actionable + needs-context only
        today_items = [r for r in today_items
                       if r.get("triage_grade", "") in ("actionable", "needs-context")]

        if today_items:
            # Group by theme first, then by tag within theme
            by_theme = {}
            for r in today_items:
                theme = r.get("theme", "Other") or "Other"
                by_theme.setdefault(theme, []).append(r)

            sections.append(f"**Today's highlights ({len(today_items)} actionable items):**")
            for theme, items in sorted(by_theme.items()):
                sections.append(f"\n**{theme}:**")
                # Sort by score, show top items
                sorted_items = sorted(items, key=lambda x: -x.get("triage_score", 0))
                for item in sorted_items[:4]:
                    tag = item["tag"]
                    sections.append(f"- [{tag}] {item['text'][:80]}")
                if len(items) > 4:
                    sections.append(f"  ...+{len(items) - 4} more in this area")
        else:
            sections.append("*No actionable session notes captured today.*")
    finally:
        db.close()

    # VCS summary
    if repo_path:
        vcs = _get_vcs_summary(repo_path, since_hours=12)
        if vcs:
            sections.append(f"\n**Commits today:**\n{vcs}")

    # Open blocker count
    bdb = BlockerDB(db_path)
    try:
        stats = bdb.get_stats()
        if stats["open"] > 0:
            sections.append(f"\n**Blockers:** {stats['open']} open"
                            f" ({stats['critical']} critical)")
    finally:
        bdb.close()

    return Briefing(
        type="wrapup",
        title="End of Day",
        body="\n".join(sections) if sections else "Quiet day. Nothing to report.",
        timestamp=datetime.now().strftime("%H:%M"),
    )


WEEKLY_PROMPT = """\
You are writing a Friday "Week in Review" for a game developer wrapping up their week.

This is personal — it's for THEM, not stakeholders. The tone is celebratory first,
honest second. Start with wins before problems.

Given the raw data below (session notes, commits, blockers), write:

## This Week's Wins
- [Things that shipped, got decided, got unblocked — celebrate these]
- [Even small wins count: "locked down the sensor chain design" is a win]

## What Got Done
- [Concrete deliverables: features built, bugs fixed, decisions made]
- [Reference commit activity where relevant]

## Blockers Cleared
- [Blockers that were resolved this week — feels good to see these gone]

## Still on the Plate
- [Open action items and unresolved blockers — no judgment, just status]

## Ideas Worth Revisiting
- [IDEAs from sessions that didn't get acted on but are worth remembering]

## Next Week's Focus
- [Based on open actions, approaching deadlines, and momentum — what should Monday look like?]

Rules:
- Lead with wins. This is a celebration of progress, not an audit.
- Be specific — "built the vine draft screen" not "made progress on UI"
- Keep it personal and direct — "you" not "the team"
- If it was a quiet week, acknowledge that honestly — rest is progress too
- Omit empty sections
- End with one encouraging line about the week ahead"""


def generate_weekly(repo_path: str = "",
                     db_path: str = DEFAULT_DB_PATH,
                     verbose: bool = False) -> Briefing | None:
    """Friday week-in-review — celebration first, status second."""
    # Only generate on Fridays
    if datetime.now().weekday() != 4:
        return None

    db = DigestDB(db_path)
    try:
        stats = db.stats()
        recent = db.recent(limit=200)
    finally:
        db.close()

    week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    week_items = [r for r in recent if r.get("session_date", "")[:10] >= week_ago]

    # Build context
    by_tag = {}
    by_theme = {}
    for r in week_items:
        by_tag.setdefault(r["tag"], []).append(r)
        by_theme.setdefault(r.get("theme", ""), []).append(r)

    context_lines = [f"Week ending {datetime.now().strftime('%Y-%m-%d')}",
                     f"Total items from sessions this week: {len(week_items)}",
                     ""]

    for tag in ["DECISION", "ACTION", "BLOCKER", "IDEA", "QUESTION", "WATCH"]:
        items = by_tag.get(tag, [])
        if items:
            context_lines.append(f"{tag} ({len(items)}):")
            for item in items[:10]:
                context_lines.append(f"  - {item['text']}")
            context_lines.append("")

    if by_theme:
        context_lines.append("THEMES WORKED ON:")
        for theme, items in sorted(by_theme.items(), key=lambda x: -len(x[1])):
            if theme:
                context_lines.append(f"  {theme}: {len(items)} items")
        context_lines.append("")

    # Blocker stats
    bdb = BlockerDB(db_path)
    try:
        bstats = bdb.get_stats()
        all_blockers = bdb.get_all_blockers(limit=20)
        resolved_this_week = [b for b in all_blockers
                              if b.status == "resolved"
                              and b.resolved_at[:10] >= week_ago]
        context_lines.append(f"Blockers: {bstats['open']} still open, "
                             f"{len(resolved_this_week)} resolved this week")
        if resolved_this_week:
            context_lines.append("Resolved blockers:")
            for b in resolved_this_week:
                context_lines.append(f"  - {b.text[:70]}")
        open_blockers = [b for b in all_blockers if b.status == "open"]
        if open_blockers:
            context_lines.append("Still open:")
            for b in open_blockers[:5]:
                context_lines.append(f"  - {b.text[:70]} ({b.age_days}d old)")
        context_lines.append("")
    finally:
        bdb.close()

    # VCS — full week
    if repo_path:
        vcs = _get_vcs_summary(repo_path, since_hours=168)
        if vcs:
            context_lines.append(f"COMMITS THIS WEEK:\n{vcs}")
            # Also count total
            try:
                from vcs_monitor import GitBackend
                backend = GitBackend(repo_path)
                since = datetime.now() - timedelta(days=7)
                all_changes = backend.recent_changes(since, limit=100)
                total_files = sum(len(c.files) for c in all_changes)
                context_lines.append(f"\nTotal: {len(all_changes)} commits, "
                                     f"{total_files} files touched")
            except Exception:
                pass
        context_lines.append("")

    context = "\n".join(context_lines)

    # Even with no session data, still generate if there are commits
    if not week_items and not repo_path:
        return None

    # Claude-formatted review
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            client = anthropic.Anthropic()
            response = client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=WEEKLY_PROMPT,
                messages=[{"role": "user", "content": context}],
            )
            body = response.content[0].text
        except Exception:
            body = _fallback_weekly(context_lines, by_tag, week_items)
    else:
        body = _fallback_weekly(context_lines, by_tag, week_items)

    return Briefing(
        type="weekly",
        title="Week in Review",
        body=body,
        timestamp=datetime.now().strftime("%H:%M"),
    )


def _fallback_weekly(context_lines: list, by_tag: dict,
                      week_items: list) -> str:
    """Fallback formatting when Claude isn't available."""
    sections = []

    decisions = by_tag.get("DECISION", [])
    actions = by_tag.get("ACTION", [])
    ideas = by_tag.get("IDEA", [])

    if decisions:
        sections.append("**Decisions locked this week:**")
        for d in decisions[:8]:
            sections.append(f"- {d['text'][:70]}")

    if actions:
        sections.append(f"\n**Action items ({len(actions)}):**")
        for a in actions[:8]:
            sections.append(f"- {a['text'][:70]}")

    if ideas:
        sections.append(f"\n**Ideas to revisit:**")
        for i in ideas[:5]:
            sections.append(f"- {i['text'][:70]}")

    if not sections:
        sections.append("*Quiet week on the session front. "
                        "Check commits for what actually shipped.*")

    return "\n".join(sections)


def generate_nag(db_path: str = DEFAULT_DB_PATH,
                  verbose: bool = False) -> Briefing | None:
    """Follow-up nag for stale high-priority action items."""
    db = DigestDB(db_path)
    try:
        actions = db.search_by_tag("ACTION", limit=50)
    finally:
        db.close()

    nag_items = []
    for a in actions:
        try:
            session_date = a.get("session_date", "")[:10]
            age = (datetime.now() - datetime.strptime(session_date, "%Y-%m-%d")).days
            score = a.get("triage_score", 0)
            if age >= 5 and score >= 60:
                nag_items.append((age, score, a))
        except (ValueError, TypeError):
            pass

    if not nag_items:
        return None

    nag_items.sort(key=lambda x: (-x[1], -x[0]))

    lines = [f"**{len(nag_items)} action items need attention:**\n"]
    for age, score, a in nag_items[:5]:
        lines.append(f"- [{age}d old, {score}/100] {a['text'][:65]}")
        lines.append(f"  _{a.get('theme', '')} — session {a.get('session_date', '')[:10]}_")

    if len(nag_items) > 5:
        lines.append(f"\n...and {len(nag_items) - 5} more stale items.")

    return Briefing(
        type="nag",
        title="Stale Actions",
        body="\n".join(lines),
        timestamp=datetime.now().strftime("%H:%M"),
        priority="high",
    )


# ---------------------------------------------------------------------------
# Scheduler — fires briefings at configured times
# ---------------------------------------------------------------------------

class BriefingScheduler:
    """Runs on a background thread, checks the clock, fires briefings.

    on_briefing(briefing: Briefing) — called when a briefing is ready.
    """

    def __init__(self, stop_event: threading.Event,
                 on_briefing=None,
                 standup_hour: int = 9,
                 checkin_hour: int = 13,
                 wrapup_hour: int = 17,
                 weekly_hour: int = 16,
                 nag_interval_hours: int = 4,
                 repo_path: str = "",
                 db_path: str = DEFAULT_DB_PATH,
                 calendar_events_fn=None,
                 verbose: bool = False):
        self.stop_event = stop_event
        self.on_briefing = on_briefing
        self.standup_hour = standup_hour
        self.checkin_hour = checkin_hour
        self.wrapup_hour = wrapup_hour
        self.weekly_hour = weekly_hour
        self.nag_interval_hours = nag_interval_hours
        self.repo_path = repo_path
        self.db_path = db_path
        self.calendar_events_fn = calendar_events_fn  # callable -> list[CalendarEvent]
        self.verbose = verbose

        # Track which briefings have fired today
        self._fired_today: dict[str, str] = {}  # type -> date string
        self._last_nag: float = 0
        self._startup_fired = False

    def _today(self) -> str:
        return datetime.now().strftime("%Y-%m-%d")

    def _already_fired(self, briefing_type: str) -> bool:
        return self._fired_today.get(briefing_type) == self._today()

    def _mark_fired(self, briefing_type: str):
        self._fired_today[briefing_type] = self._today()

    def _fire(self, briefing: Briefing | None):
        if briefing is None:
            return
        self._mark_fired(briefing.type)
        if self.verbose:
            print(f"  [briefing] firing: {briefing.type} -- {briefing.title}")
        if self.on_briefing:
            self.on_briefing(briefing)

    def _get_calendar_events(self) -> list[CalendarEvent]:
        if self.calendar_events_fn:
            try:
                return self.calendar_events_fn()
            except Exception:
                pass
        return []

    def _check_schedule(self):
        now = datetime.now()
        hour = now.hour

        # Startup briefing — fire once on first check (login moment)
        if not self._startup_fired:
            self._startup_fired = True
            # Only fire if within standup window (not if starting at 11pm)
            if 6 <= hour <= 11:
                if not self._already_fired("standup"):
                    briefing = generate_standup(
                        calendar_events=self._get_calendar_events(),
                        repo_path=self.repo_path,
                        db_path=self.db_path,
                        verbose=self.verbose,
                    )
                    self._fire(briefing)
                    return

        # Standup — morning
        if hour == self.standup_hour and not self._already_fired("standup"):
            briefing = generate_standup(
                calendar_events=self._get_calendar_events(),
                repo_path=self.repo_path,
                db_path=self.db_path,
                verbose=self.verbose,
            )
            self._fire(briefing)

        # Midday check-in
        elif hour == self.checkin_hour and not self._already_fired("checkin"):
            briefing = generate_checkin(
                repo_path=self.repo_path,
                db_path=self.db_path,
                verbose=self.verbose,
            )
            self._fire(briefing)

        # End of day wrap-up
        elif hour == self.wrapup_hour and not self._already_fired("wrapup"):
            briefing = generate_wrapup(
                repo_path=self.repo_path,
                db_path=self.db_path,
                verbose=self.verbose,
            )
            self._fire(briefing)

        # Weekly — Friday afternoon
        elif (hour == self.weekly_hour and now.weekday() == 4
              and not self._already_fired("weekly")):
            briefing = generate_weekly(
                repo_path=self.repo_path,
                db_path=self.db_path,
                verbose=self.verbose,
            )
            self._fire(briefing)

        # Nag — periodic, not tied to a specific hour
        if (time.time() - self._last_nag > self.nag_interval_hours * 3600
                and 9 <= hour <= 18):
            briefing = generate_nag(
                db_path=self.db_path,
                verbose=self.verbose,
            )
            if briefing:
                self._last_nag = time.time()
                self._fire(briefing)

    def run(self):
        """Blocking — runs until stop_event is set."""
        if self.verbose:
            print(f"  [briefing] scheduler started -- "
                  f"standup={self.standup_hour}:00, "
                  f"checkin={self.checkin_hour}:00, "
                  f"wrapup={self.wrapup_hour}:00")

        while not self.stop_event.is_set():
            try:
                self._check_schedule()
            except Exception as e:
                if self.verbose:
                    print(f"  [briefing] error: {e}")

            # Check every 60 seconds
            self.stop_event.wait(timeout=60.0)
