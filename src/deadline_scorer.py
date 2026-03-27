"""Deadline Scorer — escalates triage scores based on calendar proximity.

Takes existing triage results and adjusts scores based on:
1. How close a deadline event is (today's playtest = max urgency)
2. Whether the item's theme matches the deadline's subject
3. Time decay — ACTION items get more urgent as they age without resolution

Works as a post-processor on top of the existing triage scoring system.
"""

import re
from datetime import datetime, timedelta
from dataclasses import dataclass

from calendar_monitor import CalendarEvent
from triage import route_to_theme, load_training, DEFAULT_THEME_KEYWORDS


# ---------------------------------------------------------------------------
# Score adjustments
# ---------------------------------------------------------------------------

# How much to boost scores based on deadline proximity
DEADLINE_BOOST = {
    "today": 20,        # deadline is today — max urgency
    "tomorrow": 12,     # deadline is tomorrow
    "this_week": 6,     # deadline within 7 days
    "next_week": 2,     # deadline within 14 days
}

# How much to boost for matching theme between item and deadline
THEME_MATCH_BOOST = 8

# Age-based escalation for ACTION items (days since session)
AGE_ESCALATION = {
    7: 5,     # 1 week old, unresolved
    14: 10,   # 2 weeks old
    21: 15,   # 3 weeks old — this should be screaming
}

# Keywords that suggest an event is a deadline vs just a meeting
DEADLINE_KEYWORDS = [
    "deadline", "due", "release", "ship", "playtest", "review",
    "demo", "milestone", "freeze", "cutoff", "launch", "submit",
    "delivery", "handoff", "final",
]

# Keywords that suggest an event is a meeting worth prepping for
MEETING_KEYWORDS = [
    "sync", "standup", "retro", "planning", "sprint", "review",
    "1:1", "one on one", "check-in", "checkin", "catch up",
    "discussion", "brainstorm", "design review", "playtest",
]


# ---------------------------------------------------------------------------
# Deadline proximity calculation
# ---------------------------------------------------------------------------

def _deadline_proximity(event: CalendarEvent) -> str | None:
    """Categorize how close a deadline is."""
    now = datetime.now()
    delta = event.start - now

    if delta.total_seconds() < 0:
        return None  # already passed

    if delta.days == 0:
        return "today"
    elif delta.days == 1:
        return "tomorrow"
    elif delta.days <= 7:
        return "this_week"
    elif delta.days <= 14:
        return "next_week"
    return None


def _is_deadline_event(event: CalendarEvent) -> bool:
    """Check if a calendar event looks like a deadline."""
    subject_lower = event.subject.lower()
    return any(kw in subject_lower for kw in DEADLINE_KEYWORDS)


def _is_meeting_event(event: CalendarEvent) -> bool:
    """Check if a calendar event looks like a meeting worth prepping for."""
    subject_lower = event.subject.lower()
    return any(kw in subject_lower for kw in MEETING_KEYWORDS)


def _infer_event_theme(event: CalendarEvent,
                       theme_keywords: dict = None) -> str:
    """Infer the project theme of a calendar event from its subject/body."""
    text = f"{event.subject} {event.body_preview}"
    return route_to_theme(text, theme_keywords)


# ---------------------------------------------------------------------------
# Score adjustment
# ---------------------------------------------------------------------------

@dataclass
class ScoreAdjustment:
    """A single adjustment applied to a triage item's score."""
    reason: str
    delta: int
    source: str   # "deadline", "age", "theme_match"


def adjust_item_score(item: dict, calendar_events: list[CalendarEvent],
                      session_date: str = "",
                      theme_keywords: dict = None) -> dict:
    """Adjust a single triage item's score based on calendar context.

    Takes a dict with at minimum: tag, text, theme, triage_score, triage_grade
    Returns the same dict with adjusted score/grade and an 'adjustments' list.
    """
    if theme_keywords is None:
        theme_keywords = load_training()

    adjustments: list[ScoreAdjustment] = []
    original_score = item.get("triage_score", 0)
    score = original_score

    item_theme = item.get("theme", "")
    item_tag = item.get("tag", "")

    # 1. Deadline proximity boost
    for event in calendar_events:
        if not _is_deadline_event(event):
            continue

        proximity = _deadline_proximity(event)
        if proximity is None:
            continue

        boost = DEADLINE_BOOST.get(proximity, 0)
        if boost == 0:
            continue

        # Extra boost if the item's theme matches the deadline's theme
        event_theme = _infer_event_theme(event, theme_keywords)
        theme_match = (event_theme == item_theme and item_theme)

        if theme_match:
            boost += THEME_MATCH_BOOST
            adjustments.append(ScoreAdjustment(
                reason=f"Theme matches {proximity} deadline: {event.subject}",
                delta=THEME_MATCH_BOOST,
                source="theme_match",
            ))

        adjustments.append(ScoreAdjustment(
            reason=f"{proximity.replace('_', ' ').title()} deadline: {event.subject}",
            delta=boost - (THEME_MATCH_BOOST if theme_match else 0),
            source="deadline",
        ))
        score += boost

        # Only apply the closest/most urgent deadline
        break

    # 2. Age-based escalation for ACTION items
    if item_tag == "ACTION" and session_date:
        try:
            session_dt = datetime.strptime(session_date[:10], "%Y-%m-%d")
            age_days = (datetime.now() - session_dt).days

            for threshold_days, boost in sorted(AGE_ESCALATION.items(), reverse=True):
                if age_days >= threshold_days:
                    adjustments.append(ScoreAdjustment(
                        reason=f"ACTION item is {age_days} days old with no resolution",
                        delta=boost,
                        source="age",
                    ))
                    score += boost
                    break
        except (ValueError, TypeError):
            pass

    # 3. Meeting prep boost — if an upcoming meeting matches the item theme
    for event in calendar_events:
        if not _is_meeting_event(event):
            continue

        proximity = _deadline_proximity(event)
        if proximity not in ("today", "tomorrow"):
            continue

        event_theme = _infer_event_theme(event, theme_keywords)
        if event_theme == item_theme and item_theme:
            boost = 5 if proximity == "today" else 3
            adjustments.append(ScoreAdjustment(
                reason=f"Relevant to {proximity}'s meeting: {event.subject}",
                delta=boost,
                source="meeting_prep",
            ))
            score += boost
            break

    # Cap at 100
    score = min(100, score)

    # Re-grade based on adjusted score
    if score >= 80:
        grade = "actionable"
    elif score >= 55:
        grade = "needs-context"
    elif score >= 30:
        grade = "parked"
    else:
        grade = "stale"

    result = dict(item)
    result["triage_score"] = score
    result["triage_grade"] = grade
    result["original_score"] = original_score
    result["adjustments"] = adjustments
    return result


def adjust_batch_scores(items: list[dict],
                        calendar_events: list[CalendarEvent],
                        theme_keywords: dict = None,
                        verbose: bool = False) -> list[dict]:
    """Adjust scores for a batch of triage items based on calendar context.

    Returns the adjusted items sorted by score (highest first).
    """
    adjusted = []
    upgrades = 0

    for item in items:
        result = adjust_item_score(
            item, calendar_events,
            session_date=item.get("session_date", ""),
            theme_keywords=theme_keywords,
        )
        if result["triage_score"] > result.get("original_score", 0):
            upgrades += 1
        adjusted.append(result)

    adjusted.sort(key=lambda x: x["triage_score"], reverse=True)

    if verbose and upgrades:
        print(f"  [deadline] adjusted {upgrades}/{len(items)} items based on calendar")

    return adjusted


# ---------------------------------------------------------------------------
# Convenience: get deadline-aware priorities
# ---------------------------------------------------------------------------

def get_deadline_priorities(calendar_events: list[CalendarEvent],
                            db_path: str = None,
                            limit: int = 10,
                            verbose: bool = False) -> list[dict]:
    """Pull actionable items from digest DB and re-score with deadline awareness.

    Returns the top N items by adjusted score.
    """
    from digest_db import DigestDB, DEFAULT_DB_PATH

    db = DigestDB(db_path or DEFAULT_DB_PATH)
    try:
        # Get recent actionable and needs-context items
        items = db.search_by_grade("actionable", limit=50)
        items += db.search_by_grade("needs-context", limit=30)
    finally:
        db.close()

    if not items:
        return []

    adjusted = adjust_batch_scores(items, calendar_events, verbose=verbose)
    return adjusted[:limit]
