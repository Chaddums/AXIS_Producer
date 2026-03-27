"""Meeting Assistant — pre-meeting briefs and post-meeting action sweeps.

Pre-meeting: Assembles what was discussed before, what actions were committed,
what VCS activity has happened, and deadline-adjusted priorities.

Post-meeting: Generates a structured action item summary from the session,
suitable for pasting into Slack or email as a follow-up.
"""

import os
from datetime import datetime

import anthropic

from calendar_monitor import CalendarEvent
from deadline_scorer import get_deadline_priorities, _infer_event_theme
from digest_db import DigestDB, DEFAULT_DB_PATH
from triage import load_training


MODEL = "claude-sonnet-4-20250514"
MAX_TOKENS = 1024


# ---------------------------------------------------------------------------
# Pre-meeting brief
# ---------------------------------------------------------------------------

PRE_MEETING_PROMPT = """\
You are preparing a 1-page meeting brief for a game developer about to join a meeting.

Given:
1. The meeting subject and details
2. Relevant items from past sessions (decisions, actions, questions)
3. Recent VCS activity related to this topic
4. Deadline-adjusted priority items

Write a concise brief with these sections (omit empty ones):
## Context — what this meeting is likely about (1-2 sentences)
## Last Time — key decisions/actions from previous sessions on this topic
## Progress — what VCS activity shows has been done
## Open Items — unresolved questions and uncommitted actions
## Priorities — what's most urgent given upcoming deadlines

Rules:
- Be terse. Bullet points, not paragraphs.
- Focus on what the person needs to KNOW and DO before walking in.
- If there's nothing relevant from past sessions, say so briefly."""


def generate_pre_meeting_brief(event: CalendarEvent,
                                calendar_events: list[CalendarEvent] = None,
                                vcs_changes: list = None,
                                db_path: str = DEFAULT_DB_PATH,
                                verbose: bool = False) -> str:
    """Generate a pre-meeting brief for an upcoming meeting.

    Returns markdown text suitable for display or clipboard.
    """
    theme_keywords = load_training()
    event_theme = _infer_event_theme(event, theme_keywords)

    # 1. Pull relevant items from digest DB
    db = DigestDB(db_path)
    try:
        # Search by event subject keywords
        subject_words = [w for w in event.subject.lower().split()
                         if len(w) >= 4]
        relevant_items = []

        if subject_words:
            query = " ".join(subject_words[:5])
            relevant_items = db.search(query, limit=15)

        # Also pull items matching the inferred theme
        if event_theme:
            theme_items = db.search_by_theme(event_theme, limit=10)
            # Merge without duplicates
            seen_texts = {r["text"][:40] for r in relevant_items}
            for item in theme_items:
                if item["text"][:40] not in seen_texts:
                    relevant_items.append(item)

        # Pull open action items
        action_items = db.search_by_tag("ACTION", limit=15)
        open_questions = db.search_by_tag("QUESTION", limit=10)
    finally:
        db.close()

    # 2. Get deadline-adjusted priorities
    priorities = []
    if calendar_events:
        priorities = get_deadline_priorities(
            calendar_events, db_path=db_path, limit=5, verbose=verbose)

    # 3. Format context for Claude
    sections = []

    sections.append(f"Meeting: {event.subject}")
    sections.append(f"Time: {event.start:%H:%M} - {event.end:%H:%M}")
    if event.location:
        sections.append(f"Location: {event.location}")
    if event.organizer:
        sections.append(f"Organizer: {event.organizer}")
    sections.append("")

    if relevant_items:
        sections.append("RELEVANT PAST ITEMS:")
        for item in relevant_items[:10]:
            tag = item.get("tag", "")
            theme = item.get("theme", "")
            text = item.get("text", "")
            date = item.get("session_date", "")
            sections.append(f"  [{tag}] ({theme}) {text} — session: {date}")
        sections.append("")

    if action_items:
        sections.append("OPEN ACTION ITEMS:")
        for item in action_items[:8]:
            score = item.get("triage_score", 0)
            grade = item.get("triage_grade", "")
            sections.append(f"  [{score}/100 {grade}] {item['text']}")
        sections.append("")

    if open_questions:
        sections.append("OPEN QUESTIONS:")
        for item in open_questions[:5]:
            sections.append(f"  - {item['text']}")
        sections.append("")

    if priorities:
        sections.append("DEADLINE-ADJUSTED PRIORITIES:")
        for item in priorities[:5]:
            score = item.get("triage_score", 0)
            adjustments = item.get("adjustments", [])
            adj_reasons = "; ".join(a.reason for a in adjustments) if adjustments else "no adjustments"
            sections.append(f"  [{score}/100] {item['text']} ({adj_reasons})")
        sections.append("")

    if vcs_changes:
        sections.append("RECENT VCS ACTIVITY:")
        for change in vcs_changes[:5]:
            sections.append(f"  {change.id} — {change.author}: {change.message}")
        sections.append("")

    context = "\n".join(sections)

    # 4. If no relevant data, return a simple stub
    if not relevant_items and not action_items and not priorities:
        return (
            f"# Pre-Meeting Brief: {event.subject}\n"
            f"**{event.start:%H:%M}** — {event.duration_minutes}min\n\n"
            f"No relevant items found in session history for this meeting.\n"
            f"This may be a new topic or first discussion.\n"
        )

    # 5. Send to Claude for assembly
    if not os.environ.get("ANTHROPIC_API_KEY"):
        # Fallback: return raw context without Claude formatting
        return f"# Pre-Meeting Brief: {event.subject}\n\n{context}"

    try:
        client = anthropic.Anthropic()
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=PRE_MEETING_PROMPT,
            messages=[{"role": "user", "content": context}],
        )
        brief = response.content[0].text

        header = (
            f"# Pre-Meeting Brief: {event.subject}\n"
            f"**{event.start:%H:%M}** — {event.duration_minutes}min"
            f"{' — ' + event.location if event.location else ''}\n"
            f"*Generated {datetime.now():%H:%M}*\n\n"
        )
        return header + brief

    except Exception as e:
        if verbose:
            print(f"  [meeting] brief generation error: {e}")
        return f"# Pre-Meeting Brief: {event.subject}\n\n{context}"


# ---------------------------------------------------------------------------
# Post-meeting action sweep
# ---------------------------------------------------------------------------

ACTION_SWEEP_PROMPT = """\
You are a producer writing a follow-up message after a game development meeting.

Given the session notes below (decisions, actions, questions from the meeting),
write a brief follow-up suitable for posting to Slack or email.

Format:
## Follow-Up: [meeting name]
[1 sentence summary of what was discussed]

### Action Items
- [ ] [specific action] — [owner if mentioned]

### Decisions Made
- [decision stated as fact]

### Still Open
- [unresolved question or deferred item]

### Next Steps
- [what happens next, when to reconvene]

Rules:
- Be terse. One line per item.
- Use checkboxes for action items (Slack/GitHub compatible).
- If owner isn't clear, write "— owner TBD"
- Only include sections that have content.
- End with a timestamp line."""


def generate_action_sweep(log_path: str,
                           meeting_name: str = "",
                           db_path: str = DEFAULT_DB_PATH,
                           verbose: bool = False) -> str:
    """Generate a post-meeting action sweep from the session log.

    Reads the most recent session log, extracts action items and decisions,
    and formats them as a follow-up message.

    Returns markdown text suitable for Slack/email/clipboard.
    """
    # 1. Read the session log
    if not os.path.exists(log_path):
        return "No session log found — was recording active during the meeting?"

    with open(log_path, "r", encoding="utf-8") as f:
        raw = f.read()

    if not raw.strip():
        return "Session log is empty — no transcript was captured."

    # 2. Also pull from digest DB for this session's items
    from digest import parse_session_log, extract_session_header

    session_start = extract_session_header(raw)
    batches = parse_session_log(raw)

    if not batches:
        return "No structured batches found in session log."

    # Flatten all items
    all_items = []
    for batch in batches:
        for item in batch["items"]:
            all_items.append(f"[{item['tag']}] {item['text']} ({batch['time']})")

    items_text = "\n".join(all_items)
    total_items = len(all_items)

    if verbose:
        print(f"  [meeting] action sweep: {total_items} items from {len(batches)} batches")

    # 3. Build context
    context = f"Meeting: {meeting_name or 'Dev Session'}\n"
    context += f"Session started: {session_start}\n"
    context += f"Total items: {total_items}\n\n"
    context += "SESSION NOTES:\n"
    context += items_text

    # 4. Send to Claude
    if not os.environ.get("ANTHROPIC_API_KEY"):
        # Fallback without Claude
        header = f"## Follow-Up: {meeting_name or 'Dev Session'}\n"
        header += f"*{session_start}*\n\n"
        actions = [i for i in all_items if "[ACTION]" in i]
        decisions = [i for i in all_items if "[DECISION]" in i]
        questions = [i for i in all_items if "[QUESTION]" in i]

        sections = []
        if actions:
            sections.append("### Action Items\n" + "\n".join(f"- [ ] {a}" for a in actions))
        if decisions:
            sections.append("### Decisions Made\n" + "\n".join(f"- {d}" for d in decisions))
        if questions:
            sections.append("### Still Open\n" + "\n".join(f"- {q}" for q in questions))

        return header + "\n\n".join(sections) + f"\n\n---\n*Generated {datetime.now():%Y-%m-%d %H:%M}*"

    try:
        client = anthropic.Anthropic()
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=ACTION_SWEEP_PROMPT,
            messages=[{"role": "user", "content": context}],
        )
        sweep = response.content[0].text
        return sweep + f"\n\n---\n*Generated {datetime.now():%Y-%m-%d %H:%M} by AXIS Producer*"

    except Exception as e:
        if verbose:
            print(f"  [meeting] sweep generation error: {e}")
        return f"Error generating action sweep: {e}"


def copy_to_clipboard(text: str):
    """Copy text to clipboard for easy pasting into Slack/email."""
    try:
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        root.clipboard_clear()
        root.clipboard_append(text)
        root.update()
        root.destroy()
        return True
    except Exception:
        return False
