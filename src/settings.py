"""Persistent settings for the AXIS Producer tray app."""

import json
import os
from dataclasses import dataclass, field, asdict

def _settings_dir():
    """Get the directory for settings — handles PyInstaller bundles."""
    if getattr(sys, 'frozen', False):
        # PyInstaller: store settings next to the .exe, not inside _internal
        return os.path.dirname(sys.executable)
    return os.path.dirname(__file__)

import sys
SETTINGS_PATH = os.path.join(_settings_dir(), "tray_settings.json")

WORKSPACE_PRESETS = {
    "dev_team":      {"slack_monitor": True,  "vcs_monitor": True,  "calendar_monitor": True, "email_monitor": True,  "claude_monitor": True,  "privacy_preset": "standard"},
    "business_team": {"slack_monitor": True,  "vcs_monitor": False, "calendar_monitor": True, "email_monitor": True,  "claude_monitor": False, "privacy_preset": "standard"},
    "healthcare":    {"slack_monitor": False, "vcs_monitor": False, "calendar_monitor": True, "email_monitor": True,  "claude_monitor": False, "privacy_preset": "hipaa_aware"},
    "agency":        {"slack_monitor": True,  "vcs_monitor": False, "calendar_monitor": True, "email_monitor": True,  "claude_monitor": False, "privacy_preset": "standard"},
}

# Terminology overrides per workspace type — remaps LLM output headers and dashboard labels
WORKSPACE_TERMINOLOGY = {
    "dev_team": {
        "Decisions Locked": "Decisions Locked",
        "Ideas Generated": "Ideas Generated",
        "Open Questions": "Open Questions",
        "Action Items": "Action Items",
        "Watch List": "Watch List",
        "Blockers": "Blockers",
        "Key Discussion": "Key Discussion",
        # Dashboard panel labels
        "_panel_workstreams": "Branches",
        "_panel_blocked": "Blockers",
        "_panel_alerts": "Conflicts & Alerts",
    },
    "business_team": {
        "Decisions Locked": "Decisions Made",
        "Ideas Generated": "Ideas & Proposals",
        "Open Questions": "Open Questions",
        "Action Items": "Action Items",
        "Watch List": "Risks & Concerns",
        "Blockers": "Waiting On",
        "Key Discussion": "Key Takeaways",
        "_panel_workstreams": "Projects",
        "_panel_blocked": "Waiting On",
        "_panel_alerts": "Deadlines & Flags",
    },
    "healthcare": {
        "Decisions Locked": "Treatment Plans",
        "Ideas Generated": "Notes & Observations",
        "Open Questions": "Follow-up Questions",
        "Action Items": "Follow-ups",
        "Watch List": "Monitor",
        "Blockers": "Pending",
        "Key Discussion": "Case Notes",
        "_panel_workstreams": "Patients",
        "_panel_blocked": "Pending",
        "_panel_alerts": "Urgent Flags",
    },
    "agency": {
        "Decisions Locked": "Decisions Locked",
        "Ideas Generated": "Creative Ideas",
        "Open Questions": "Open Questions",
        "Action Items": "Deliverables",
        "Watch List": "At Risk",
        "Blockers": "Pending Approval",
        "Key Discussion": "Key Discussion",
        "_panel_workstreams": "Clients",
        "_panel_blocked": "Pending Approval",
        "_panel_alerts": "Deadlines",
    },
}

# System prompt context injected per workspace type
WORKSPACE_PROMPT_CONTEXT = {
    "dev_team": (
        "This is a software development team. They discuss code, architecture, "
        "bugs, features, git branches, deployments, and technical tradeoffs. "
        "Blockers typically involve dependencies, code reviews, or tooling issues."
    ),
    "business_team": (
        "This is a business team. They discuss projects, strategy, clients, "
        "revenue, timelines, and organizational decisions. 'Blockers' here means "
        "anything someone is waiting on before they can proceed — approvals, "
        "deliverables from another team, vendor responses, budget sign-off."
    ),
    "healthcare": (
        "This is a healthcare team. They discuss patients, treatments, scheduling, "
        "and clinical observations. Use clinical framing: 'Treatment Plans' not "
        "'Decisions', 'Follow-ups' not 'Action Items', 'Pending' not 'Blockers'. "
        "Never include patient identifiers (names, DOB, SSN) in output — use "
        "role descriptions only (e.g. 'the patient with the knee issue')."
    ),
    "agency": (
        "This is a creative/consulting agency. They discuss client work, campaigns, "
        "deliverables, timelines, and creative direction. 'Blockers' here means "
        "pending client approvals, asset delivery, or stakeholder feedback."
    ),
}


@dataclass
class Settings:
    mic_device: int | None = None        # None = system default
    loopback_device: int | None = None   # None = auto-detect WASAPI output
    whisper_model: str = "base.en"
    batch_interval: int = 300            # seconds between Claude batches
    log_dir: str = "."                   # where session_log.md goes
    auto_detect: bool = True             # start detecting on launch
    vad_sensitivity: int = 1             # 0-3 for webrtcvad (low = more sensitive)
    chat_monitor: bool = True            # monitor clipboard for chat text
    slack_monitor: bool = True            # monitor Slack channels
    slack_channel_ids: list[str] = None   # None = auto-discover joined channels
    slack_poll_interval: float = 15.0     # seconds between Slack polls
    email_monitor: bool = True            # monitor Outlook inbox
    email_poll_interval: float = 30.0     # seconds between email polls
    email_unread_only: bool = True        # only process unread emails
    focus_advisor: bool = True            # cross-ref messages with digest DB
    vcs_monitor: bool = True              # track git/p4v activity
    vcs_repo_path: str | None = None      # None = auto-detect from cwd
    vcs_poll_interval: float = 120.0      # seconds between VCS polls
    calendar_monitor: bool = True         # monitor Outlook calendar
    calendar_poll_interval: float = 60.0  # seconds between calendar polls
    pre_meeting_minutes: int = 10         # generate brief N minutes before meeting
    roadmap_path: str | None = None       # None = auto-detect ALPHA_ROADMAP.md
    daily_briefings: bool = True          # enable scheduled briefings
    standup_hour: int = 9                 # morning standup hour (24h)
    checkin_hour: int = 13                # midday check-in hour
    wrapup_hour: int = 17                 # end-of-day wrap-up hour
    nag_interval_hours: int = 4           # hours between stale action nags
    cloud_sync: bool = False              # push events to shared Supabase DB
    supabase_url: str = ""                # Supabase project URL
    supabase_key: str = ""                # Supabase anon/service key
    user_identity: str = "stu"            # who am I (shown in shared DB)
    synthesis_interval: int = 900         # seconds between cross-stream synthesis
    claude_monitor: bool = True           # watch Claude Code conversation files
    claude_project_paths: list[str] = None  # None = auto-discover all projects
    claude_poll_interval: float = 3.0     # seconds between JSONL polls
    notification_level: str = "info"      # minimum priority to show: ambient, info, warning, critical
    aggressive_alerts: bool = False       # beep on warning, system alert on critical
    dashboard_port: int = 8080            # localhost port for dashboard
    verbose: bool = False

    # LLM provider
    llm_provider: str = "ollama"          # ollama | anthropic | openai | google | groq
    llm_api_key: str = ""                 # user's own API key (blank for ollama)
    llm_model: str = ""                   # override model name (blank = provider default)
    ollama_url: str = "http://localhost:11434"

    # Backend integration
    backend_url: str = "https://axisproducer-production.up.railway.app"
    auth_token: str = ""                  # JWT from backend login/signup
    user_id: str = ""                     # backend user UUID
    team_id: str = ""                     # active team UUID

    # Setup tracking
    setup_completed_at: str = ""          # ISO timestamp when NUX completed

    # Workspace type system
    workspace_type: str = "custom"        # dev_team | business_team | healthcare | agency | custom
    workspace_context: str = ""           # free text injected into Claude system prompt
    output_terminology: dict = None       # label overrides, e.g. {"Blockers": "Waiting on"}
    privacy_preset: str = "standard"      # standard | strict | hipaa_aware

    def __post_init__(self):
        if self.slack_channel_ids is None:
            self.slack_channel_ids = []
        if self.claude_project_paths is None:
            self.claude_project_paths = []
        if self.output_terminology is None:
            self.output_terminology = {}

    def apply_workspace_preset(self, workspace_type: str):
        """Apply a workspace preset — monitor flags, terminology, and prompt context."""
        preset = WORKSPACE_PRESETS.get(workspace_type)
        if not preset:
            return
        self.workspace_type = workspace_type
        for key, value in preset.items():
            setattr(self, key, value)

        # Apply terminology overrides (skip _panel_ keys — those are for the dashboard)
        terminology = WORKSPACE_TERMINOLOGY.get(workspace_type, {})
        self.output_terminology = {k: v for k, v in terminology.items() if not k.startswith("_")}

        # Apply workspace context for LLM prompt
        prompt_ctx = WORKSPACE_PROMPT_CONTEXT.get(workspace_type, "")
        if prompt_ctx:
            self.workspace_context = prompt_ctx

        self.save()

    def get_panel_labels(self) -> dict:
        """Get dashboard panel labels for the current workspace type."""
        terminology = WORKSPACE_TERMINOLOGY.get(self.workspace_type, {})
        return {
            "workstreams": terminology.get("_panel_workstreams", "Branches"),
            "blocked": terminology.get("_panel_blocked", "Blockers"),
            "alerts": terminology.get("_panel_alerts", "Conflicts & Alerts"),
        }

    @property
    def log_path(self) -> str:
        return os.path.join(self.log_dir, "session_log.md")

    def save(self):
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, indent=2)

    @classmethod
    def load(cls) -> "Settings":
        if not os.path.exists(SETTINGS_PATH):
            s = cls()
            s.save()
            return s
        try:
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            return cls(**{k: v for k, v in data.items()
                          if k in cls.__dataclass_fields__})
        except (json.JSONDecodeError, TypeError):
            return cls()
