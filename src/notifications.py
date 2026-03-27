"""Notification Priority System — classifies and routes all AXIS Producer events.

Central notification router. All events flow through here to get a priority
classification before being shown to the user.

Priority tiers:
    critical  — must dismiss manually, red pulsing, beep
    warning   — orange, 15s auto-dismiss
    info      — cyan, 8s auto-dismiss
    success   — green, 6s auto-dismiss
    ambient   — grey, 4s auto-dismiss
"""

from dataclasses import dataclass, field


# Priority levels ordered from lowest to highest
PRIORITY_LEVELS = ["ambient", "info", "success", "warning", "critical"]

PRIORITY_CONFIG = {
    "critical": {
        "color": "#ff0000",
        "border": 3,
        "sound": True,
        "auto_dismiss_ms": 0,       # must dismiss manually
        "size": (420, 140),
    },
    "warning": {
        "color": "#ff8800",
        "border": 2,
        "sound": False,
        "auto_dismiss_ms": 15000,
        "size": (400, 100),
    },
    "info": {
        "color": "#4ac0ff",
        "border": 1,
        "sound": False,
        "auto_dismiss_ms": 8000,
        "size": (380, 70),
    },
    "success": {
        "color": "#34d399",
        "border": 1,
        "sound": False,
        "auto_dismiss_ms": 6000,
        "size": (380, 60),
    },
    "ambient": {
        "color": "#64748b",
        "border": 0,
        "sound": False,
        "auto_dismiss_ms": 4000,
        "size": (360, 50),
    },
}


@dataclass
class Notification:
    """A classified notification ready for display."""
    title: str
    body: str
    priority: str           # critical, warning, info, success, ambient
    source: str             # git, claude_code, voice, team, synthesis, chat
    details: str = ""       # optional extra line
    files: list[str] = field(default_factory=list)
    action_label: str = ""  # optional button text
    action_callback: object = None  # callable

    @property
    def config(self) -> dict:
        return PRIORITY_CONFIG.get(self.priority, PRIORITY_CONFIG["info"])


def should_show(notification: Notification, min_level: str = "info") -> bool:
    """Check if a notification meets the minimum display threshold."""
    try:
        notif_idx = PRIORITY_LEVELS.index(notification.priority)
        min_idx = PRIORITY_LEVELS.index(min_level)
        return notif_idx >= min_idx
    except ValueError:
        return True


def classify_git_event(event_type: str, raw: dict = None) -> str:
    """Classify a git event into a priority level."""
    raw = raw or {}

    if event_type in ("merge_conflict",):
        return "critical"
    if event_type == "merge":
        return "info"
    if event_type == "unpushed":
        count = raw.get("count", 0)
        return "warning" if count > 5 else "info"
    if event_type == "unpulled":
        return "info"
    if event_type == "divergence":
        behind = raw.get("behind", 0)
        return "warning" if behind > 10 else "info"
    if event_type == "branch_status":
        unpushed = raw.get("unpushed", 0)
        if unpushed > 0:
            return "info"
        return "ambient"
    if event_type == "commit":
        return "info"
    return "ambient"


def classify_team_event(event: dict) -> str:
    """Classify a remote team event into a priority level."""
    event_type = event.get("event_type", "")

    if event_type in ("blocker", "blockers"):
        return "warning"
    if event_type == "blocker_resolved":
        return "success"
    if event_type == "file_conflict":
        return "critical"

    # Check for file conflicts in raw data
    raw = event.get("raw", {})
    if raw.get("conflict"):
        return "critical"

    return "ambient"


def classify_voice_event(category: str) -> str:
    """Classify a voice batch item by category."""
    cat = category.lower()
    if "blocker" in cat:
        return "warning"
    if "decision" in cat:
        return "info"
    if "action" in cat:
        return "info"
    if "question" in cat:
        return "info"
    return "ambient"


def classify_event(stream: str, event_type: str, raw: dict = None) -> str:
    """Classify any event into a priority level."""
    raw = raw or {}

    if stream in ("git", "git_branch", "git_health"):
        return classify_git_event(event_type, raw)

    if stream == "voice":
        return classify_voice_event(event_type)

    if stream == "synthesis":
        return "info"

    if stream == "chat":
        return "info"

    if stream == "claude_code":
        if event_type in ("file_edit", "write"):
            return "info"
        if event_type == "user_message":
            return "ambient"
        return "ambient"

    return "ambient"


def make_notification(title: str, body: str, stream: str,
                      event_type: str, raw: dict = None,
                      files: list[str] = None,
                      details: str = "",
                      priority_override: str = None) -> Notification:
    """Create a Notification with auto-classified priority."""
    priority = priority_override or classify_event(stream, event_type, raw)

    return Notification(
        title=title,
        body=body,
        priority=priority,
        source=stream,
        details=details,
        files=files or [],
    )


# --- Convenience builders for common notification types ---

def git_alert(alert_type: str, message: str, raw: dict = None) -> Notification:
    """Build a notification from a GitHealthAlert."""
    priority = classify_git_event(alert_type, raw)
    titles = {
        "unpushed": "Unpushed Commits",
        "unpulled": "Remote Changes Available",
        "divergence": "Branch Divergence",
        "merge_conflict": "Merge Conflict",
    }
    return Notification(
        title=titles.get(alert_type, alert_type.replace("_", " ").title()),
        body=message,
        priority=priority,
        source="git",
    )


def remote_event(event: dict) -> Notification:
    """Build a notification from a remote team event."""
    who = event.get("who", "?")
    stream = event.get("stream", "?")
    summary = event.get("summary", "")[:100]
    priority = classify_team_event(event)

    return Notification(
        title=f"{who} ({stream})",
        body=summary,
        priority=priority,
        source="team",
        files=event.get("files", []),
    )


def blocker_alert(event_type: str, blocker) -> Notification:
    """Build a notification from a blocker event."""
    severity = getattr(blocker, "severity", "")
    is_critical = severity == "critical"

    if event_type == "resolved":
        priority = "success"
        title = "Blocker Resolved"
    elif event_type == "escalated" or is_critical:
        priority = "critical"
        title = "Critical Blocker" if is_critical else "Blocker Escalated"
    else:
        priority = "warning"
        title = "New Blocker"

    return Notification(
        title=title,
        body=getattr(blocker, "text", str(blocker))[:120],
        priority=priority,
        source="voice",
        details=f"Owner: {getattr(blocker, 'owner', '?')}",
    )


def synthesis_ready(summary: str) -> Notification:
    """Build a notification for a team synthesis."""
    return Notification(
        title="Team Synthesis Ready",
        body=summary[:120] + ("..." if len(summary) > 120 else ""),
        priority="info",
        source="synthesis",
    )


def scope_alert(alert) -> Notification:
    """Build a notification from a scope alert."""
    alert_type = getattr(alert, "type", "")
    if alert_type == "cut_item":
        priority = "warning"
        title = "Cut Item Detected"
    elif alert_type == "overcommit":
        priority = "warning"
        title = "Overcommitment Warning"
    else:
        priority = "info"
        title = "Scope Alert"

    severity = getattr(alert, "severity", "")
    if severity == "critical":
        priority = "critical"

    return Notification(
        title=title,
        body=getattr(alert, "message", str(alert))[:120],
        priority=priority,
        source="voice",
    )


def vcs_insight(insight) -> Notification:
    """Build a notification from a VCS insight."""
    itype = getattr(insight, "type", "")
    type_config = {
        "progress": ("info", "Progress"),
        "drift": ("warning", "Drift Detected"),
        "stall": ("warning", "Work Stalled"),
        "untracked": ("info", "Untracked Work"),
    }
    priority, title = type_config.get(itype, ("info", itype.title()))

    return Notification(
        title=title,
        body=getattr(insight, "summary", str(insight))[:120],
        priority=priority,
        source="git",
    )
