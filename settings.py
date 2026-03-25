"""Persistent settings for the AXIS Producer tray app."""

import json
import os
from dataclasses import dataclass, field, asdict

SETTINGS_PATH = os.path.join(os.path.dirname(__file__), "tray_settings.json")

WORKSPACE_PRESETS = {
    "dev_team":      {"slack_monitor": True,  "vcs_monitor": True,  "calendar_monitor": True, "email_monitor": True,  "claude_monitor": True,  "privacy_preset": "standard"},
    "business_team": {"slack_monitor": True,  "vcs_monitor": False, "calendar_monitor": True, "email_monitor": True,  "claude_monitor": False, "privacy_preset": "standard"},
    "healthcare":    {"slack_monitor": False, "vcs_monitor": False, "calendar_monitor": True, "email_monitor": True,  "claude_monitor": False, "privacy_preset": "hipaa_aware"},
    "agency":        {"slack_monitor": True,  "vcs_monitor": False, "calendar_monitor": True, "email_monitor": True,  "claude_monitor": False, "privacy_preset": "standard"},
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
        """Apply a workspace preset, setting monitor flags and privacy level."""
        preset = WORKSPACE_PRESETS.get(workspace_type)
        if not preset:
            return
        self.workspace_type = workspace_type
        for key, value in preset.items():
            setattr(self, key, value)
        self.save()

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
