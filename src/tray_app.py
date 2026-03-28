#!/usr/bin/env python3
"""AXIS Producer — System Tray App.

Sits in the Windows notification area. Auto-detects voice conversation,
offers to start recording. Captures mic + system audio + clipboard chat.

Hotkey: Win+Shift+R to toggle recording.
Edge indicator: thin red bar at top of screen when recording.
Auto-stop: 2 minutes of silence or end-of-day.

Usage:
    python tray_app.py
"""

import os
import sys
import threading
import time
import webbrowser
from datetime import datetime

import keyboard
import pystray

from session_controller import SessionController, State
from meeting_assistant import copy_to_clipboard
from daily_briefing import Briefing
from settings import Settings
from tray_icons import icon_idle, icon_detecting, icon_recording
from desktop_indicator import DesktopIndicator
from notifications import (
    Notification, PRIORITY_CONFIG, should_show,
    git_alert, remote_event, blocker_alert, synthesis_ready,
    scope_alert, vcs_insight, make_notification,
)


HOTKEY = "win+shift+r"
SILENCE_TIMEOUT = 120  # seconds of silence before auto-stop
END_OF_DAY_CHECK_INTERVAL = 300  # check every 5 minutes


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

        # Recording prompt state
        self._prompt_pending = False
        self._prompt_timer: threading.Timer | None = None

        # Focus alert history
        self._focus_history: list = []

        # Desktop edge indicator
        self._indicator = DesktopIndicator()

        # Silence tracking for auto-stop
        self._last_speech_time = 0.0
        self._silence_checker: threading.Timer | None = None

        # End-of-day checker
        self._eod_checker: threading.Timer | None = None

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
                "Start Session  (Win+Shift+R)",
                self._on_start_session,
                visible=lambda item: self.controller.state in (
                    State.IDLE, State.DETECTING),
            ),
            pystray.MenuItem(
                "Stop Session  (Win+Shift+R)",
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
                "Start with Windows",
                self._on_toggle_startup,
                checked=lambda item: self.settings.start_with_windows,
            ),
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
            self._indicator.show_recording()
        elif state in (State.DETECTING, State.PROMPTED):
            self._icon.icon = icon_detecting()
            self._indicator.show_detecting()
        else:
            self._icon.icon = icon_idle()
            self._indicator.hide()
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

    # ----- Hotkey -----

    def _on_hotkey(self):
        """Win+Shift+R — toggle recording on/off."""
        state = self.controller.state
        if state == State.RECORDING:
            self._notify("AXIS", "Stopping session...")
            threading.Thread(target=self.controller.stop_recording,
                             name="hotkey-stop", daemon=True).start()
        elif state in (State.IDLE, State.DETECTING):
            self._notify("AXIS", "Recording started")
            self.controller.start_recording()
        elif state == State.PROMPTED:
            # Accept the pending prompt
            self._on_confirm_prompt(None, None)

    # ----- Silence auto-stop -----

    def _start_silence_checker(self):
        """Start monitoring for extended silence during recording."""
        self._last_speech_time = time.time()
        self._check_silence()

    def _stop_silence_checker(self):
        if self._silence_checker:
            self._silence_checker.cancel()
            self._silence_checker = None

    def _check_silence(self):
        """Periodic check — auto-stop if silence exceeds threshold."""
        if self.controller.state != State.RECORDING:
            return
        elapsed = time.time() - self._last_speech_time
        if elapsed > SILENCE_TIMEOUT:
            print(f"  [auto-stop] {SILENCE_TIMEOUT}s silence — stopping session")
            self._notify("AXIS", f"Session auto-stopped after {SILENCE_TIMEOUT // 60} min silence")
            threading.Thread(target=self.controller.stop_recording,
                             name="silence-stop", daemon=True).start()
            return
        # Check again in 15 seconds
        self._silence_checker = threading.Timer(15.0, self._check_silence)
        self._silence_checker.daemon = True
        self._silence_checker.start()

    def _on_speech_activity(self):
        """Called when any audio activity is detected — resets silence timer."""
        self._last_speech_time = time.time()

    # ----- End of day auto-stop -----

    def _start_eod_checker(self):
        """Periodically check if it's past end-of-day."""
        self._check_eod()

    def _stop_eod_checker(self):
        if self._eod_checker:
            self._eod_checker.cancel()
            self._eod_checker = None

    def _check_eod(self):
        """Auto-stop recording at end of day."""
        now = datetime.now()
        eod_hour = self.settings.wrapup_hour + 1  # 1 hour after wrapup
        if now.hour >= eod_hour and self.controller.state == State.RECORDING:
            print(f"  [auto-stop] end of day ({eod_hour}:00) — stopping session")
            self._notify("AXIS", "End of day — session stopped. Notes saved.")
            threading.Thread(target=self.controller.stop_recording,
                             name="eod-stop", daemon=True).start()
            return
        # Check again in 5 minutes
        self._eod_checker = threading.Timer(END_OF_DAY_CHECK_INTERVAL, self._check_eod)
        self._eod_checker.daemon = True
        self._eod_checker.start()

    # ----- Windows startup -----

    def _on_toggle_startup(self, icon, item):
        self.settings.start_with_windows = not self.settings.start_with_windows
        self.settings.save()
        _set_windows_startup(self.settings.start_with_windows)

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
        self._cleanup()
        self._icon.stop()

        import subprocess
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "launcher.py")
        subprocess.Popen(
            [sys.executable, script],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )

    def _on_exit(self, icon, item):
        self._cleanup()
        self._icon.stop()

    def _cleanup(self):
        """Stop all services cleanly."""
        self._stop_silence_checker()
        self._stop_eod_checker()
        self._indicator.stop()
        try:
            keyboard.unhook_all()
        except Exception:
            pass
        self.controller.stop_briefings()
        if self.controller.state == State.RECORDING:
            self.controller.stop_recording()
        elif self.controller.state == State.DETECTING:
            self.controller.stop_detecting()

    # ----- Recording prompt -----

    def _on_confirm_prompt(self, icon, item):
        self._prompt_pending = False
        if self._prompt_timer:
            self._prompt_timer.cancel()
            self._prompt_timer = None
        if self._icon:
            self._icon.update_menu()
        self.controller.on_prompt_response(True)

    def _on_dismiss_prompt(self, icon, item):
        self._prompt_pending = False
        if self._prompt_timer:
            self._prompt_timer.cancel()
            self._prompt_timer = None
        if self._icon:
            self._icon.update_menu()
        self.controller.on_prompt_response(False)

    def _expire_prompt(self):
        if self._prompt_pending:
            self._prompt_pending = False
            if self._icon:
                self._icon.update_menu()
            self.controller.on_prompt_response(False)

    # ----- State change callback -----

    def _on_state_change(self, state: State):
        self._update_icon()
        # Start/stop silence checker based on recording state
        if state == State.RECORDING:
            self._start_silence_checker()
        else:
            self._stop_silence_checker()

    def _on_speech_detected(self):
        """Called from VadDetector thread — show notification + tray menu prompt."""
        self._on_speech_activity()  # reset silence timer
        self._prompt_pending = True
        if self._icon:
            self._icon.update_menu()
        self._notify(
            "AXIS Producer",
            "Conversation detected. Press Win+Shift+R or right-click tray to record."
        )
        self._prompt_timer = threading.Timer(15.0, self._expire_prompt)
        self._prompt_timer.daemon = True
        self._prompt_timer.start()

    def _on_focus_match(self, match):
        self._focus_history.append(match)
        if len(self._focus_history) > 50:
            self._focus_history = self._focus_history[-50:]
        preview = match.message_preview[:80] + ("..." if len(match.message_preview) > 80 else "")
        self._notify(
            f"[{match.priority}] {match.source}",
            f"{preview}\nMatches: [{match.matched_tag}] {match.matched_item[:60]}"
        )

    def _on_vcs_insight(self, insight):
        type_icons = {"progress": ">>", "drift": "<>", "stall": "!!", "untracked": "??"}
        icon = type_icons.get(insight.type, "--")
        summary = insight.summary[:90] + ("..." if len(insight.summary) > 90 else "")
        self._notify(f"{icon} [{insight.priority}] {insight.type.upper()}", summary)

    def _on_meeting_approaching(self, event, brief_text):
        mins = max(0, int(event.minutes_until_start))
        self._notify(
            f"Meeting in {mins} min: {event.subject}",
            brief_text[:150] + ("..." if len(brief_text) > 150 else "")
        )
        copy_to_clipboard(brief_text)

    def _on_meeting_ended(self, event):
        pass

    def _on_brief_ready(self, brief_text):
        pass

    def _on_sweep_ready(self, sweep_text):
        self._notify("Post-Meeting Action Sweep",
                     sweep_text[:150] + ("..." if len(sweep_text) > 150 else ""))
        copy_to_clipboard(sweep_text)

    def _on_blocker(self, event_type, blocker):
        type_titles = {"new": "NEW BLOCKER", "escalated": "BLOCKER ESCALATED", "resolved": "BLOCKER RESOLVED"}
        title = type_titles.get(event_type, "BLOCKER")
        if blocker.severity == "critical" and event_type != "resolved":
            title = "CRITICAL BLOCKER" if event_type == "new" else "CRITICAL ESCALATED"
        text_preview = blocker.text[:90] + ("..." if len(blocker.text) > 90 else "")
        self._notify(title, text_preview)
        if blocker.severity == "critical" and event_type != "resolved":
            try:
                import winsound
                winsound.Beep(1000, 200)
            except Exception:
                pass

    def _on_briefing(self, briefing):
        self._notify(f"AXIS — {briefing.display_title}",
                     briefing.body[:200] + ("..." if len(briefing.body) > 200 else ""))
        copy_to_clipboard(briefing.body)

    def _on_scope_alert(self, alert):
        type_titles = {"cut_item": "SCOPE: CUT ITEM", "scope_creep": "SCOPE: CREEP DETECTED", "overcommit": "CAPACITY: OVERLOADED"}
        title = type_titles.get(alert.type, "SCOPE ALERT")
        self._notify(title, alert.message)

    def _on_items_logged(self, items):
        self._on_speech_activity()  # batch means there was speech
        if not items:
            return
        count = len(items)
        cats = {}
        for cat, text in items:
            cats.setdefault(cat, []).append(text)
        lines = [f"{cat}: {len(texts)}" for cat, texts in cats.items()]
        self._notify(f"AXIS — {count} item{'s' if count != 1 else ''} logged", ", ".join(lines))

    def _on_remote_event(self, event):
        notif = remote_event(event)
        if should_show(notif, self.settings.notification_level):
            who = event.get("who", "?")
            stream = event.get("stream", "?")
            summary = event.get("summary", "")[:100]
            self._notify(f"{who} ({stream})", summary)

    def _on_claude_event(self, event):
        pass

    def _on_synthesis(self, summary):
        self._notify("Team Activity Synthesis",
                     summary[:200] + ("..." if len(summary) > 200 else ""))

    # ----- Run -----

    def run(self):
        """Start the tray app (blocks on main thread)."""
        if not os.environ.get("ANTHROPIC_API_KEY"):
            print("WARNING: ANTHROPIC_API_KEY not set -- recording will fail")

        # Start desktop edge indicator
        self._indicator.start()

        # Register global hotkey
        try:
            keyboard.add_hotkey(HOTKEY, self._on_hotkey, suppress=False)
            print(f"  Hotkey: {HOTKEY} to toggle recording")
        except Exception as e:
            print(f"  Hotkey registration failed: {e}")

        # Auto-start detection if configured
        if self.settings.auto_detect and self.settings.auth_token:
            threading.Timer(1.0, self.controller.start_detecting).start()

        # Start briefing scheduler
        if self.settings.auth_token:
            threading.Timer(2.0, self.controller.start_briefings).start()

        # Start cloud services
        threading.Timer(3.0, self.controller.start_cloud_services).start()

        # Start end-of-day checker
        self._start_eod_checker()

        print("AXIS Producer tray app started")
        print("  Win+Shift+R to start/stop recording")
        print("  Right-click tray icon for options")

        # Run pystray on main thread (Win32 message pump)
        self._icon.run()

        # Cleanup after icon stops
        self._cleanup()


def _set_windows_startup(enabled: bool):
    """Add or remove AXIS Producer from Windows startup."""
    import winreg
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    app_name = "AXIS Producer"

    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE)
        if enabled:
            # Point to the launcher
            if getattr(sys, 'frozen', False):
                exe_path = sys.executable
            else:
                exe_path = f'"{sys.executable}" "{os.path.join(os.path.dirname(__file__), "launcher.py")}"'
            winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ, exe_path)
            print(f"  Added to Windows startup: {app_name}")
        else:
            try:
                winreg.DeleteValue(key, app_name)
                print(f"  Removed from Windows startup: {app_name}")
            except FileNotFoundError:
                pass
        winreg.CloseKey(key)
    except Exception as e:
        print(f"  Startup registration failed: {e}")


def main():
    app = TrayApp()
    app.run()
