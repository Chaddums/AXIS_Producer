#!/usr/bin/env python3
"""AXIS Producer — System Tray App.

Sits in the Windows notification area. Auto-detects voice conversation,
offers to start recording. Captures mic + system audio + clipboard chat.

Usage:
    python tray_app.py
"""

import os
import sys
import threading
import webbrowser

import pystray

from session_controller import SessionController, State
from meeting_assistant import copy_to_clipboard
from daily_briefing import Briefing
from settings import Settings
from tray_icons import icon_idle, icon_detecting, icon_recording
from notifications import (
    Notification, PRIORITY_CONFIG, should_show,
    git_alert, remote_event, blocker_alert, synthesis_ready,
    scope_alert, vcs_insight, make_notification,
)


class TrayApp:
    """System tray application for AXIS Producer."""

    def __init__(self):
        self.settings = Settings.load()
        self.controller = SessionController(
            settings=self.settings,
            on_state_change=self._on_state_change,
            on_speech_detected=self._on_speech_detected,
            on_focus_match=self._on_focus_match,
            on_vcs_insight=self._on_vcs_insight,
            on_meeting_approaching=self._on_meeting_approaching,
            on_meeting_ended=self._on_meeting_ended,
            on_brief_ready=self._on_brief_ready,
            on_sweep_ready=self._on_sweep_ready,
            on_blocker=self._on_blocker,
            on_briefing=self._on_briefing,
            on_scope_alert=self._on_scope_alert,
            on_items_logged=self._on_items_logged,
            on_remote_event=self._on_remote_event,
            on_claude_event=self._on_claude_event,
            on_synthesis=self._on_synthesis,
        )

        # Recording prompt state — auto-accept with notification
        self._prompt_pending = False
        self._prompt_timer: threading.Timer | None = None

        # Focus alert history
        self._focus_history: list = []

        # Tray icon
        self._icon: pystray.Icon | None = None
        self._build_icon()

    # ----- Tray icon -----

    def _build_icon(self):
        self._icon = pystray.Icon(
            name="AXIS Producer",
            icon=icon_idle(),
            title="AXIS Producer — idle",
            menu=self._build_menu(),
        )

    def _build_menu(self) -> pystray.Menu:
        return pystray.Menu(
            pystray.MenuItem(
                "Start Session",
                self._on_start_session,
                visible=lambda item: self.controller.state in (
                    State.IDLE, State.DETECTING),
            ),
            pystray.MenuItem(
                "Stop Session",
                self._on_stop_session,
                visible=lambda item: self.controller.state == State.RECORDING,
            ),
            pystray.MenuItem(
                "Force Batch Now",
                self._on_force_batch,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Confirm Recording",
                self._on_confirm_prompt,
                visible=lambda item: self._prompt_pending,
            ),
            pystray.MenuItem(
                "Dismiss Prompt",
                self._on_dismiss_prompt,
                visible=lambda item: self._prompt_pending,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Open Session Log",
                self._on_open_log,
            ),
            pystray.MenuItem(
                "Open Dashboard",
                self._on_open_dashboard,
            ),
            pystray.MenuItem(
                "Open Setup",
                self._on_open_nux,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Private Mode",
                self._on_toggle_private,
                checked=lambda item: (
                    self.controller._cloud_sync.private_mode
                    if self.controller._cloud_sync else False
                ),
            ),
            pystray.MenuItem(
                "Verbose",
                self._on_toggle_verbose,
                checked=lambda item: self.settings.verbose,
            ),
            pystray.MenuItem("Restart", self._on_restart),
            pystray.MenuItem("Exit", self._on_exit),
        )

    def _update_icon(self):
        if not self._icon:
            return
        state = self.controller.state
        if state == State.RECORDING:
            self._icon.icon = icon_recording()
        elif state in (State.DETECTING, State.PROMPTED):
            self._icon.icon = icon_detecting()
        else:
            self._icon.icon = icon_idle()
        self._icon.title = self.controller.status_text()

    def _notify(self, title: str, message: str):
        """Show a Windows balloon notification via pystray."""
        if self._icon:
            try:
                self._icon.notify(message, title)
            except Exception:
                pass

    def _dashboard_url(self, path: str = "dashboard.html") -> str:
        port = self.settings.dashboard_port
        return f"http://localhost:{port}/{path}"

    # ----- Menu handlers -----

    def _on_start_session(self, icon, item):
        self.controller.start_recording()

    def _on_stop_session(self, icon, item):
        threading.Thread(target=self.controller.stop_recording,
                         name="stop-recording", daemon=True).start()

    def _on_force_batch(self, icon, item):
        self.controller.force_batch()

    def _on_open_log(self, icon, item):
        log_path = os.path.join(os.path.abspath(self.settings.log_dir),
                                "session_log.md")
        if os.path.exists(log_path):
            os.startfile(log_path)
        else:
            print(f"  No session log found at: {log_path}")

    def _on_open_dashboard(self, icon, item):
        webbrowser.open(self._dashboard_url())

    def _on_open_nux(self, icon, item):
        webbrowser.open(self._dashboard_url("nux.html"))

    def _on_toggle_private(self, icon, item):
        sync = self.controller._cloud_sync
        if sync:
            sync.private_mode = not sync.private_mode

    def _on_toggle_verbose(self, icon, item):
        self.settings.verbose = not self.settings.verbose
        self.settings.save()

    def _on_restart(self, icon, item):
        """Restart the entire AXIS Producer process."""
        self.controller.stop_briefings()
        if self.controller.state == State.RECORDING:
            self.controller.stop_recording()
        elif self.controller.state == State.DETECTING:
            self.controller.stop_detecting()
        self._icon.stop()

        # Re-launch the same entry point in a new process
        import subprocess
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "launcher.py")
        subprocess.Popen(
            [sys.executable, script],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )

    def _on_exit(self, icon, item):
        self.controller.stop_briefings()
        if self.controller.state == State.RECORDING:
            self.controller.stop_recording()
        elif self.controller.state == State.DETECTING:
            self.controller.stop_detecting()
        self._icon.stop()

    # ----- Recording prompt (replaces tkinter dialog) -----

    def _on_confirm_prompt(self, icon, item):
        """User confirmed recording via tray menu."""
        self._prompt_pending = False
        if self._prompt_timer:
            self._prompt_timer.cancel()
            self._prompt_timer = None
        self._icon.update_menu()
        self.controller.on_prompt_response(True)

    def _on_dismiss_prompt(self, icon, item):
        """User dismissed recording prompt via tray menu."""
        self._prompt_pending = False
        if self._prompt_timer:
            self._prompt_timer.cancel()
            self._prompt_timer = None
        self._icon.update_menu()
        self.controller.on_prompt_response(False)

    def _expire_prompt(self):
        """Auto-dismiss prompt after timeout."""
        if self._prompt_pending:
            self._prompt_pending = False
            if self._icon:
                self._icon.update_menu()
            self.controller.on_prompt_response(False)

    # ----- State change callback -----

    def _on_state_change(self, state: State):
        self._update_icon()

    def _on_speech_detected(self):
        """Called from VadDetector thread — show notification + tray menu prompt."""
        self._prompt_pending = True
        if self._icon:
            self._icon.update_menu()
        self._notify(
            "AXIS Producer",
            "Conversation detected. Right-click tray icon to confirm recording."
        )
        # Auto-dismiss after 15 seconds
        self._prompt_timer = threading.Timer(15.0, self._expire_prompt)
        self._prompt_timer.daemon = True
        self._prompt_timer.start()

    def _on_focus_match(self, match):
        """Called from FocusAdvisor when a message matches a DB priority."""
        self._focus_history.append(match)
        if len(self._focus_history) > 50:
            self._focus_history = self._focus_history[-50:]

        preview = match.message_preview[:80] + ("..." if len(match.message_preview) > 80 else "")
        self._notify(
            f"[{match.priority}] {match.source}",
            f"{preview}\nMatches: [{match.matched_tag}] {match.matched_item[:60]}"
        )

    def _on_vcs_insight(self, insight):
        """Called from VcsMonitor when it detects progress/drift/stall."""
        type_icons = {"progress": ">>", "drift": "<>", "stall": "!!", "untracked": "??"}
        icon = type_icons.get(insight.type, "--")
        summary = insight.summary[:90] + ("..." if len(insight.summary) > 90 else "")
        self._notify(
            f"{icon} [{insight.priority}] {insight.type.upper()}",
            summary,
        )

    def _on_meeting_approaching(self, event, brief_text):
        """Called when a meeting is approaching with the generated brief."""
        mins = max(0, int(event.minutes_until_start))
        self._notify(
            f"Meeting in {mins} min: {event.subject}",
            brief_text[:150] + ("..." if len(brief_text) > 150 else "")
        )
        # Copy brief to clipboard automatically
        copy_to_clipboard(brief_text)

    def _on_meeting_ended(self, event):
        """Called when a meeting ends."""
        pass  # sweep is handled via on_sweep_ready

    def _on_brief_ready(self, brief_text):
        """Called when a pre-meeting brief is generated."""
        pass  # handled via on_meeting_approaching which includes the brief

    def _on_sweep_ready(self, sweep_text):
        """Called when a post-meeting action sweep is generated."""
        self._notify(
            "Post-Meeting Action Sweep",
            sweep_text[:150] + ("..." if len(sweep_text) > 150 else "")
        )
        # Copy to clipboard for easy pasting
        copy_to_clipboard(sweep_text)

    def _on_blocker(self, event_type, blocker):
        """Called from BlockerTracker on new/escalated/resolved blockers."""
        type_titles = {
            "new": "NEW BLOCKER",
            "escalated": "BLOCKER ESCALATED",
            "resolved": "BLOCKER RESOLVED",
        }
        title = type_titles.get(event_type, "BLOCKER")
        if blocker.severity == "critical" and event_type != "resolved":
            title = "CRITICAL BLOCKER" if event_type == "new" else "CRITICAL ESCALATED"

        text_preview = blocker.text[:90] + ("..." if len(blocker.text) > 90 else "")
        details = []
        if blocker.owner:
            details.append(f"Who: {blocker.owner}")
        if blocker.dependency:
            details.append(f"Waiting: {blocker.dependency}")
        detail_line = " | ".join(details) if details else ""

        self._notify(title, f"{text_preview}\n{detail_line}")

        # Beep on critical
        if blocker.severity == "critical" and event_type != "resolved":
            try:
                import winsound
                winsound.Beep(1000, 200)
            except Exception:
                pass

    def _on_briefing(self, briefing):
        """Called from BriefingScheduler when a briefing is ready."""
        self._notify(
            f"AXIS — {briefing.display_title}",
            briefing.body[:200] + ("..." if len(briefing.body) > 200 else "")
        )
        # Copy briefing to clipboard for convenience
        copy_to_clipboard(briefing.body)

    def _on_scope_alert(self, alert):
        """Called from ScopeGuard on scope creep or overcommitment."""
        type_titles = {
            "cut_item": "SCOPE: CUT ITEM",
            "scope_creep": "SCOPE: CREEP DETECTED",
            "overcommit": "CAPACITY: OVERLOADED",
        }
        title = type_titles.get(alert.type, "SCOPE ALERT")
        trigger = alert.trigger_text[:80] + ("..." if len(alert.trigger_text) > 80 else "")
        self._notify(title, f"{alert.message}\nHeard: \"{trigger}\"")

    def _on_items_logged(self, items):
        """Called after each batch — show notification summarizing logged items."""
        if not items:
            return
        count = len(items)
        # Group by category
        cats = {}
        for cat, text in items:
            cats.setdefault(cat, []).append(text)

        lines = []
        for cat, texts in cats.items():
            lines.append(f"{cat}: {len(texts)}")
        summary = ", ".join(lines)

        self._notify(
            f"AXIS — {count} item{'s' if count != 1 else ''} logged",
            summary,
        )

    def _on_remote_event(self, event):
        """Called when another team member's event arrives via cloud sync."""
        notif = remote_event(event)
        if should_show(notif, self.settings.notification_level):
            who = event.get("who", "?")
            stream = event.get("stream", "?")
            summary = event.get("summary", "")[:100]
            self._notify(f"{who} ({stream})", summary)

    def _on_claude_event(self, event):
        """Called when Claude Code activity is detected locally."""
        pass  # local events are informational; only surface remote events

    def _on_synthesis(self, summary):
        """Called when a team activity synthesis is generated."""
        notif = synthesis_ready(summary)
        self._notify(
            "Team Activity Synthesis",
            summary[:200] + ("..." if len(summary) > 200 else "")
        )

    # ----- Run -----

    def run(self):
        """Start the tray app (blocks on main thread)."""
        # Verify API key
        if not os.environ.get("ANTHROPIC_API_KEY"):
            print("WARNING: ANTHROPIC_API_KEY not set -- recording will fail")

        # Auto-start detection if configured (skip during NUX — no auth yet)
        if self.settings.auto_detect and self.settings.auth_token:
            # Small delay to let icon appear first
            threading.Timer(1.0, self.controller.start_detecting).start()

        # Start briefing scheduler only if authenticated
        if self.settings.auth_token:
            threading.Timer(2.0, self.controller.start_briefings).start()

        # Start cloud services (Claude monitor + cloud sync, always-on)
        threading.Timer(3.0, self.controller.start_cloud_services).start()

        print("AXIS Producer tray app started")
        print("Right-click the tray icon for options")

        # Run pystray on main thread (Win32 message pump)
        self._icon.run()


def main():
    app = TrayApp()
    app.run()
