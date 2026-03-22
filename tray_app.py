#!/usr/bin/env python3
"""AXIS Producer — System Tray App.

Sits in the Windows notification area. Auto-detects voice conversation,
offers to start recording. Captures mic + system audio + clipboard chat.

Usage:
    python tray_app.py
"""

import os
import queue
import sys
import threading
import tkinter as tk

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

        # Tkinter runs on its own daemon thread for popup dialogs
        self._tk_queue: queue.Queue = queue.Queue()
        self._tk_root: tk.Tk | None = None
        self._tk_thread = threading.Thread(target=self._tk_loop,
                                           name="tkinter", daemon=True)

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
                "Start Listening",
                self._on_start_listening,
                visible=lambda item: self.controller.state in (State.IDLE,),
            ),
            pystray.MenuItem(
                "Stop Listening",
                self._on_stop_listening,
                visible=lambda item: self.controller.state == State.DETECTING,
            ),
            pystray.MenuItem(
                "Start Recording",
                self._on_start_recording,
                visible=lambda item: self.controller.state in (
                    State.IDLE, State.DETECTING),
            ),
            pystray.MenuItem(
                "Stop Recording",
                self._on_stop_recording,
                visible=lambda item: self.controller.state == State.RECORDING,
            ),
            pystray.MenuItem(
                "Force Batch Now",
                self._on_force_batch,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Open Session Log",
                self._on_open_log,
            ),
            pystray.MenuItem(
                "Search...",
                self._on_search,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Focus Alerts...",
                self._on_show_focus,
                visible=lambda item: self.controller.state == State.RECORDING,
            ),
            pystray.MenuItem(
                "Blockers...",
                self._on_show_blockers,
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
                "Open Dashboard",
                self._on_open_dashboard,
            ),
            pystray.MenuItem(
                "Run Setup...",
                self._on_run_setup,
            ),
            pystray.Menu.SEPARATOR,
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

    # ----- Menu handlers -----

    def _on_start_listening(self, icon, item):
        self.controller.start_detecting()

    def _on_stop_listening(self, icon, item):
        self.controller.stop_detecting()

    def _on_start_recording(self, icon, item):
        self.controller.start_recording()

    def _on_stop_recording(self, icon, item):
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

    def _on_search(self, icon, item):
        self._tk_queue.put(("search",))

    def _on_show_focus(self, icon, item):
        self._tk_queue.put(("focus",))

    def _on_show_blockers(self, icon, item):
        self._tk_queue.put(("blockers",))

    def _on_toggle_private(self, icon, item):
        sync = self.controller._cloud_sync
        if sync:
            sync.private_mode = not sync.private_mode

    def _on_open_dashboard(self, icon, item):
        import webbrowser
        port = self.settings.dashboard_port
        webbrowser.open(f"http://localhost:{port}/dashboard.html")

    def _on_run_setup(self, icon, item):
        import subprocess
        subprocess.Popen(
            ["python", "setup.py"],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )

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

    # ----- State change callback -----

    def _on_state_change(self, state: State):
        self._update_icon()

    def _on_speech_detected(self):
        """Called from VadDetector thread — show popup on tkinter thread."""
        self._tk_queue.put(("prompt",))

    def _on_focus_match(self, match):
        """Called from FocusAdvisor when a message matches a DB priority."""
        self._tk_queue.put(("focus_alert", match))

    def _on_vcs_insight(self, insight):
        """Called from VcsMonitor when it detects progress/drift/stall."""
        self._tk_queue.put(("vcs_alert", insight))

    def _on_meeting_approaching(self, event, brief_text):
        """Called when a meeting is approaching with the generated brief."""
        self._tk_queue.put(("meeting_brief", event, brief_text))

    def _on_meeting_ended(self, event):
        """Called when a meeting ends."""
        pass  # sweep is handled via on_sweep_ready

    def _on_brief_ready(self, brief_text):
        """Called when a pre-meeting brief is generated."""
        pass  # handled via on_meeting_approaching which includes the brief

    def _on_sweep_ready(self, sweep_text):
        """Called when a post-meeting action sweep is generated."""
        self._tk_queue.put(("action_sweep", sweep_text))

    def _on_blocker(self, event_type, blocker):
        """Called from BlockerTracker on new/escalated/resolved blockers."""
        self._tk_queue.put(("blocker_alert", event_type, blocker))

    def _on_briefing(self, briefing):
        """Called from BriefingScheduler when a briefing is ready."""
        self._tk_queue.put(("briefing", briefing))

    def _on_scope_alert(self, alert):
        """Called from ScopeGuard on scope creep or overcommitment."""
        self._tk_queue.put(("scope_alert", alert))

    def _on_items_logged(self, items):
        """Called after each batch — show brief taskbar toast summarizing logged items."""
        self._tk_queue.put(("items_logged", items))

    def _on_remote_event(self, event):
        """Called when another team member's event arrives via cloud sync."""
        notif = remote_event(event)
        if should_show(notif, self.settings.notification_level):
            self._tk_queue.put(("notification", notif))

    def _on_claude_event(self, event):
        """Called when Claude Code activity is detected locally."""
        pass  # local events are informational; only surface remote events

    def _on_synthesis(self, summary):
        """Called when a team activity synthesis is generated."""
        notif = synthesis_ready(summary)
        self._tk_queue.put(("notification", notif))

    # ----- Tkinter thread (for dialogs) -----

    def _tk_loop(self):
        self._tk_root = tk.Tk()
        self._tk_root.withdraw()
        self._tk_root.after(100, self._poll_tk_queue)
        self._tk_root.mainloop()

    def _poll_tk_queue(self):
        try:
            while True:
                msg = self._tk_queue.get_nowait()
                if msg[0] == "prompt":
                    self._show_prompt_dialog()
                elif msg[0] == "search":
                    self._show_search_dialog()
                elif msg[0] == "focus_alert":
                    self._show_focus_alert(msg[1])
                elif msg[0] == "focus":
                    self._show_focus_panel()
                elif msg[0] == "vcs_alert":
                    self._show_vcs_alert(msg[1])
                elif msg[0] == "meeting_brief":
                    self._show_meeting_brief(msg[1], msg[2])
                elif msg[0] == "action_sweep":
                    self._show_action_sweep(msg[1])
                elif msg[0] == "blocker_alert":
                    self._show_blocker_alert(msg[1], msg[2])
                elif msg[0] == "blockers":
                    self._show_blockers_panel()
                elif msg[0] == "briefing":
                    self._show_briefing(msg[1])
                elif msg[0] == "scope_alert":
                    self._show_scope_alert(msg[1])
                elif msg[0] == "items_logged":
                    self._show_items_toast(msg[1])
                elif msg[0] == "remote_event":
                    self._show_remote_event_toast(msg[1])
                elif msg[0] == "synthesis":
                    self._show_synthesis_toast(msg[1])
                elif msg[0] == "notification":
                    self._show_notification(msg[1])
        except queue.Empty:
            pass
        if self._tk_root:
            self._tk_root.after(100, self._poll_tk_queue)

    def _show_prompt_dialog(self):
        """Show a small popup near the tray: 'Start recording?'"""
        dialog = tk.Toplevel(self._tk_root)
        dialog.title("AXIS Producer")
        dialog.attributes("-topmost", True)
        dialog.resizable(False, False)

        # Position bottom-right
        screen_w = dialog.winfo_screenwidth()
        screen_h = dialog.winfo_screenheight()
        dialog.geometry(f"320x120+{screen_w - 340}+{screen_h - 180}")

        dialog.configure(bg="#1a1a2e")

        label = tk.Label(
            dialog,
            text="AXIS detected a conversation.\nStart recording?",
            bg="#1a1a2e", fg="#00ccff",
            font=("Consolas", 11),
            justify="center",
        )
        label.pack(pady=(15, 10))

        btn_frame = tk.Frame(dialog, bg="#1a1a2e")
        btn_frame.pack()

        result = {"accepted": False, "responded": False}

        def on_yes():
            result["accepted"] = True
            result["responded"] = True
            dialog.destroy()

        def on_no():
            result["responded"] = True
            dialog.destroy()

        def on_timeout():
            if not result["responded"]:
                on_no()

        tk.Button(btn_frame, text="Yes", command=on_yes,
                  bg="#00aa44", fg="white", font=("Consolas", 10),
                  width=8).pack(side="left", padx=10)
        tk.Button(btn_frame, text="No", command=on_no,
                  bg="#aa4444", fg="white", font=("Consolas", 10),
                  width=8).pack(side="left", padx=10)

        # Auto-dismiss after 15 seconds
        dialog.after(15000, on_timeout)

        dialog.wait_window()
        self.controller.on_prompt_response(result["accepted"])

    def _show_search_dialog(self):
        """Simple search dialog that runs digest.py search."""
        dialog = tk.Toplevel(self._tk_root)
        dialog.title("AXIS Search")
        dialog.attributes("-topmost", True)
        dialog.geometry("400x300")
        dialog.configure(bg="#1a1a2e")

        entry_frame = tk.Frame(dialog, bg="#1a1a2e")
        entry_frame.pack(fill="x", padx=10, pady=10)

        tk.Label(entry_frame, text="Search:", bg="#1a1a2e", fg="#00ccff",
                 font=("Consolas", 10)).pack(side="left")

        entry = tk.Entry(entry_frame, font=("Consolas", 10), width=30)
        entry.pack(side="left", padx=5, fill="x", expand=True)

        results_text = tk.Text(dialog, font=("Consolas", 9),
                               bg="#0a0a1e", fg="#cccccc",
                               wrap="word", state="disabled")
        results_text.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        def do_search(event=None):
            query = entry.get().strip()
            if not query:
                return
            try:
                from digest_db import DigestDB, DEFAULT_DB_PATH
                db = DigestDB(DEFAULT_DB_PATH)
                items = db.search(query, limit=20)
                db.close()

                results_text.configure(state="normal")
                results_text.delete("1.0", "end")
                if not items:
                    results_text.insert("end", "No results found.")
                else:
                    for r in items:
                        tag = r.get("tag", "")
                        theme = r.get("theme", "")
                        text = r.get("text", "")
                        results_text.insert("end",
                                            f"[{tag}] ({theme}) {text}\n\n")
                results_text.configure(state="disabled")
            except Exception as e:
                results_text.configure(state="normal")
                results_text.delete("1.0", "end")
                results_text.insert("end", f"Error: {e}")
                results_text.configure(state="disabled")

        entry.bind("<Return>", do_search)
        tk.Button(entry_frame, text="Go", command=do_search,
                  bg="#00ccff", fg="black",
                  font=("Consolas", 10)).pack(side="left", padx=5)

        entry.focus_set()

    # ----- Focus alerts -----

    def _show_focus_alert(self, match):
        """Show a brief toast notification for a focus match."""
        self._focus_history.append(match)

        # Keep last 50 alerts
        if len(self._focus_history) > 50:
            self._focus_history = self._focus_history[-50:]

        toast = tk.Toplevel(self._tk_root)
        toast.title("AXIS Focus Alert")
        toast.attributes("-topmost", True)
        toast.overrideredirect(True)

        screen_w = toast.winfo_screenwidth()
        screen_h = toast.winfo_screenheight()
        toast.geometry(f"380x140+{screen_w - 400}+{screen_h - 200}")

        priority_colors = {"HIGH": "#ff4444", "MEDIUM": "#ccaa00", "LOW": "#666666"}
        border_color = priority_colors.get(match.priority, "#666666")

        frame = tk.Frame(toast, bg=border_color, padx=2, pady=2)
        frame.pack(fill="both", expand=True)

        inner = tk.Frame(frame, bg="#1a1a2e")
        inner.pack(fill="both", expand=True)

        tk.Label(
            inner,
            text=f"[{match.priority}] {match.source}",
            bg="#1a1a2e", fg=border_color,
            font=("Consolas", 10, "bold"),
            anchor="w",
        ).pack(fill="x", padx=8, pady=(8, 2))

        preview = match.message_preview[:80] + ("..." if len(match.message_preview) > 80 else "")
        tk.Label(
            inner,
            text=preview,
            bg="#1a1a2e", fg="#cccccc",
            font=("Consolas", 9),
            anchor="w", wraplength=360,
        ).pack(fill="x", padx=8, pady=2)

        matched_text = match.matched_item[:70] + ("..." if len(match.matched_item) > 70 else "")
        tk.Label(
            inner,
            text=f"Matches: [{match.matched_tag}] {matched_text}",
            bg="#1a1a2e", fg="#00ccff",
            font=("Consolas", 8),
            anchor="w", wraplength=360,
        ).pack(fill="x", padx=8, pady=2)

        tk.Label(
            inner,
            text=f"{match.matched_theme} | Score: {match.triage_score}/100 ({match.triage_grade})",
            bg="#1a1a2e", fg="#888888",
            font=("Consolas", 8),
            anchor="w",
        ).pack(fill="x", padx=8, pady=(0, 8))

        # Click to dismiss
        for widget in [toast, frame, inner]:
            widget.bind("<Button-1>", lambda e: toast.destroy())

        # Auto-dismiss after 10 seconds
        toast.after(10000, lambda: toast.destroy() if toast.winfo_exists() else None)

    def _show_focus_panel(self):
        """Show accumulated focus alerts in a scrollable panel."""
        dialog = tk.Toplevel(self._tk_root)
        dialog.title("AXIS Focus Alerts")
        dialog.attributes("-topmost", True)
        dialog.geometry("500x400")
        dialog.configure(bg="#1a1a2e")

        # Header with stats
        focus = self.controller._focus
        stats_text = ""
        if focus:
            s = focus.stats
            stats_text = f"Checked: {s['messages_checked']} | Matches: {s['matches_found']}"

        tk.Label(
            dialog, text=f"Focus Alerts  {stats_text}",
            bg="#1a1a2e", fg="#00ccff",
            font=("Consolas", 11, "bold"),
        ).pack(fill="x", padx=10, pady=(10, 5))

        # Scrollable text area
        text_widget = tk.Text(
            dialog, font=("Consolas", 9),
            bg="#0a0a1e", fg="#cccccc",
            wrap="word", state="disabled",
        )
        text_widget.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        text_widget.configure(state="normal")
        if not self._focus_history:
            text_widget.insert("end", "No focus alerts yet.\n\n"
                               "Alerts appear when incoming Slack messages or emails\n"
                               "match items in your session digest database.")
        else:
            for match in reversed(self._focus_history):
                text_widget.insert("end", match.format_notification() + "\n\n")
        text_widget.configure(state="disabled")

    # ----- Scope alerts -----

    def _show_scope_alert(self, alert):
        """Show a scope guard alert — producer tapping your shoulder."""
        type_config = {
            "cut_item": ("#ff4444", "SCOPE: CUT ITEM"),
            "scope_creep": ("#ff8800", "SCOPE: CREEP DETECTED"),
            "overcommit": ("#ccaa00", "CAPACITY: OVERLOADED"),
        }
        color, title = type_config.get(alert.type, ("#ff8800", "SCOPE ALERT"))

        toast = tk.Toplevel(self._tk_root)
        toast.title("AXIS Scope Guard")
        toast.attributes("-topmost", True)
        toast.overrideredirect(True)

        screen_w = toast.winfo_screenwidth()
        screen_h = toast.winfo_screenheight()
        toast.geometry(f"420x150+{screen_w - 440}+{screen_h - 210}")

        frame = tk.Frame(toast, bg=color, padx=2, pady=2)
        frame.pack(fill="both", expand=True)

        inner = tk.Frame(frame, bg="#1a1a2e")
        inner.pack(fill="both", expand=True)

        # Title
        tk.Label(
            inner, text=title,
            bg="#1a1a2e", fg=color,
            font=("Consolas", 10, "bold"), anchor="w",
        ).pack(fill="x", padx=8, pady=(8, 2))

        # Main message
        tk.Label(
            inner, text=alert.message,
            bg="#1a1a2e", fg="#ffffff",
            font=("Consolas", 10), anchor="w", wraplength=400,
        ).pack(fill="x", padx=8, pady=2)

        # What they said
        trigger = alert.trigger_text[:80] + ("..." if len(alert.trigger_text) > 80 else "")
        tk.Label(
            inner, text=f'Heard: "{trigger}"',
            bg="#1a1a2e", fg="#888888",
            font=("Consolas", 8, "italic"), anchor="w", wraplength=400,
        ).pack(fill="x", padx=8, pady=2)

        # Detail
        detail_short = alert.detail.split("\n")[0][:80]
        tk.Label(
            inner, text=detail_short,
            bg="#1a1a2e", fg="#666666",
            font=("Consolas", 8), anchor="w",
        ).pack(fill="x", padx=8, pady=(0, 8))

        for widget in [toast, frame, inner]:
            widget.bind("<Button-1>", lambda e: toast.destroy())

        # Critical stays longer
        timeout = 15000 if alert.severity == "critical" else 10000
        toast.after(timeout, lambda: toast.destroy() if toast.winfo_exists() else None)

    # ----- Daily briefings -----

    def _show_briefing(self, briefing):
        """Show a daily briefing in the same popup space as other notifications.

        Small dialog, bottom-right, dismiss/copy/snooze. User's choice.
        """
        type_colors = {
            "standup": "#00ccff",
            "checkin": "#ccaa00",
            "wrapup": "#8855cc",
            "weekly": "#ffcc00",
            "nag": "#ff8800",
        }
        color = type_colors.get(briefing.type, "#00ccff")

        # Weekly gets a bigger window — it's a celebration, give it room
        if briefing.type == "weekly":
            width, height = 500, 500
        else:
            width, height = 420, 350

        dialog = tk.Toplevel(self._tk_root)
        dialog.title(f"AXIS — {briefing.display_title}")
        dialog.attributes("-topmost", True)
        dialog.resizable(True, True)

        screen_w = dialog.winfo_screenwidth()
        screen_h = dialog.winfo_screenheight()
        dialog.geometry(f"{width}x{height}+{screen_w - width - 20}+{screen_h - height - 70}")

        dialog.configure(bg="#1a1a2e")

        # Header bar
        header_frame = tk.Frame(dialog, bg="#1a1a2e")
        header_frame.pack(fill="x", padx=10, pady=(10, 5))

        tk.Label(
            header_frame,
            text=f"{briefing.display_title}",
            bg="#1a1a2e", fg=color,
            font=("Consolas", 12, "bold"),
            anchor="w",
        ).pack(side="left")

        tk.Label(
            header_frame,
            text=briefing.timestamp,
            bg="#1a1a2e", fg="#666666",
            font=("Consolas", 9),
        ).pack(side="right")

        # Content area
        text_widget = tk.Text(
            dialog, font=("Consolas", 9),
            bg="#0a0a1e", fg="#cccccc",
            wrap="word", padx=8, pady=8,
        )
        text_widget.pack(fill="both", expand=True, padx=10, pady=(0, 5))
        text_widget.insert("1.0", briefing.body)
        text_widget.configure(state="disabled")

        # Button bar
        btn_frame = tk.Frame(dialog, bg="#1a1a2e")
        btn_frame.pack(fill="x", padx=10, pady=(0, 10))

        def do_copy():
            copy_to_clipboard(briefing.body)
            copy_btn.configure(text="Copied!", state="disabled")

        def do_dismiss():
            dialog.destroy()

        def do_snooze():
            dialog.destroy()
            # Re-fire in 30 minutes
            dialog.after(1800000, lambda: self._tk_queue.put(("briefing", briefing)))

        copy_btn = tk.Button(
            btn_frame, text="Copy", command=do_copy,
            bg=color, fg="black" if briefing.type != "nag" else "white",
            font=("Consolas", 9), width=8,
        )
        copy_btn.pack(side="left", padx=(0, 5))

        tk.Button(
            btn_frame, text="Snooze 30m", command=do_snooze,
            bg="#333355", fg="#aaaaaa",
            font=("Consolas", 9), width=10,
        ).pack(side="left", padx=5)

        tk.Button(
            btn_frame, text="Dismiss", command=do_dismiss,
            bg="#333355", fg="#aaaaaa",
            font=("Consolas", 9), width=8,
        ).pack(side="right")

        # High priority briefings don't auto-dismiss
        if briefing.priority != "high":
            dialog.after(60000, lambda: dialog.destroy() if dialog.winfo_exists() else None)

    # ----- Meeting briefs & action sweeps -----

    def _show_meeting_brief(self, event, brief_text):
        """Show a pre-meeting brief in a dialog with copy-to-clipboard."""
        dialog = tk.Toplevel(self._tk_root)
        dialog.title(f"AXIS Brief: {event.subject}")
        dialog.attributes("-topmost", True)
        dialog.geometry("550x450")
        dialog.configure(bg="#1a1a2e")

        # Header
        header_frame = tk.Frame(dialog, bg="#1a1a2e")
        header_frame.pack(fill="x", padx=10, pady=(10, 5))

        mins = max(0, int(event.minutes_until_start))
        tk.Label(
            header_frame,
            text=f"Meeting in {mins} min: {event.subject}",
            bg="#1a1a2e", fg="#00ccff",
            font=("Consolas", 11, "bold"),
            anchor="w",
        ).pack(side="left", fill="x", expand=True)

        def do_copy():
            copy_to_clipboard(brief_text)
            copy_btn.configure(text="Copied!", state="disabled")

        copy_btn = tk.Button(
            header_frame, text="Copy", command=do_copy,
            bg="#00ccff", fg="black", font=("Consolas", 9),
        )
        copy_btn.pack(side="right")

        # Brief text
        text_widget = tk.Text(
            dialog, font=("Consolas", 9),
            bg="#0a0a1e", fg="#cccccc",
            wrap="word",
        )
        text_widget.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        text_widget.insert("1.0", brief_text)
        text_widget.configure(state="disabled")

    def _show_action_sweep(self, sweep_text):
        """Show a post-meeting action sweep with copy-to-clipboard."""
        dialog = tk.Toplevel(self._tk_root)
        dialog.title("AXIS Action Sweep")
        dialog.attributes("-topmost", True)
        dialog.geometry("550x400")
        dialog.configure(bg="#1a1a2e")

        header_frame = tk.Frame(dialog, bg="#1a1a2e")
        header_frame.pack(fill="x", padx=10, pady=(10, 5))

        tk.Label(
            header_frame,
            text="Post-Meeting Action Sweep",
            bg="#1a1a2e", fg="#00aa44",
            font=("Consolas", 11, "bold"),
            anchor="w",
        ).pack(side="left", fill="x", expand=True)

        def do_copy():
            copy_to_clipboard(sweep_text)
            copy_btn.configure(text="Copied!", state="disabled")

        copy_btn = tk.Button(
            header_frame, text="Copy to Clipboard", command=do_copy,
            bg="#00aa44", fg="white", font=("Consolas", 9),
        )
        copy_btn.pack(side="right")

        text_widget = tk.Text(
            dialog, font=("Consolas", 9),
            bg="#0a0a1e", fg="#cccccc",
            wrap="word",
        )
        text_widget.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        text_widget.insert("1.0", sweep_text)
        text_widget.configure(state="disabled")

        tk.Label(
            dialog,
            text="Paste this into Slack or email as your meeting follow-up",
            bg="#1a1a2e", fg="#888888",
            font=("Consolas", 8),
        ).pack(padx=10, pady=(0, 8))

    # ----- Blocker alerts -----

    def _show_blocker_alert(self, event_type, blocker):
        """Show a toast for a new/escalated/resolved blocker."""
        type_config = {
            "new": ("#ff8800", "NEW BLOCKER"),
            "escalated": ("#ff4444", "BLOCKER ESCALATED"),
            "resolved": ("#00aa44", "BLOCKER RESOLVED"),
        }
        color, title = type_config.get(event_type, ("#888888", "BLOCKER"))

        if blocker.severity == "critical" and event_type != "resolved":
            color = "#ff0000"
            title = "CRITICAL BLOCKER" if event_type == "new" else "CRITICAL ESCALATED"

        toast = tk.Toplevel(self._tk_root)
        toast.title("AXIS Blocker")
        toast.attributes("-topmost", True)
        toast.overrideredirect(True)

        screen_w = toast.winfo_screenwidth()
        screen_h = toast.winfo_screenheight()
        toast.geometry(f"400x120+{screen_w - 420}+{screen_h - 180}")

        frame = tk.Frame(toast, bg=color, padx=2, pady=2)
        frame.pack(fill="both", expand=True)

        inner = tk.Frame(frame, bg="#1a1a2e")
        inner.pack(fill="both", expand=True)

        tk.Label(
            inner, text=title,
            bg="#1a1a2e", fg=color,
            font=("Consolas", 10, "bold"), anchor="w",
        ).pack(fill="x", padx=8, pady=(8, 2))

        text_preview = blocker.text[:90] + ("..." if len(blocker.text) > 90 else "")
        tk.Label(
            inner, text=text_preview,
            bg="#1a1a2e", fg="#cccccc",
            font=("Consolas", 9), anchor="w", wraplength=380,
        ).pack(fill="x", padx=8, pady=2)

        details = []
        if blocker.owner:
            details.append(f"Who: {blocker.owner}")
        if blocker.dependency:
            details.append(f"Waiting: {blocker.dependency}")
        details.append(f"Priority: {blocker.priority_score}/100")
        if blocker.mentions > 1:
            details.append(f"Mentioned: {blocker.mentions}x")

        tk.Label(
            inner, text=" | ".join(details),
            bg="#1a1a2e", fg="#888888",
            font=("Consolas", 8), anchor="w",
        ).pack(fill="x", padx=8, pady=(0, 8))

        for widget in [toast, frame, inner]:
            widget.bind("<Button-1>", lambda e: toast.destroy())

        # Critical blockers stay longer
        timeout = 12000 if blocker.severity == "critical" else 8000
        toast.after(timeout, lambda: toast.destroy() if toast.winfo_exists() else None)

    # ----- Items logged toast -----

    def _show_items_toast(self, items: list):
        """Show a notification window summarizing items from the latest batch.
        Stays open until manually closed. Scrollable if many items."""
        if not items:
            return

        # Category → emoji mapping
        emoji = {
            "Action Items": "\u25b6",
            "Blockers": "\u26a0",
            "Open Questions": "?",
            "Decisions": "\u2713",
            "Ideas": "*",
            "Watch List": "\u25cb",
        }

        toast = tk.Toplevel(self._tk_root)
        toast.title("AXIS — Session Notes")
        toast.attributes("-topmost", True)
        toast.resizable(True, True)

        # Position in lower-right
        screen_w = toast.winfo_screenwidth()
        screen_h = toast.winfo_screenheight()
        w, h = 480, min(400, 80 + len(items) * 28)
        toast.geometry(f"{w}x{h}+{screen_w - w - 20}+{screen_h - h - 80}")

        # Title bar with close button
        title_frame = tk.Frame(toast, bg="#1a2a44")
        title_frame.pack(fill="x")

        tk.Label(
            title_frame, text=f"AXIS — {len(items)} item{'s' if len(items) != 1 else ''} logged",
            bg="#1a2a44", fg="#4ac0ff",
            font=("Consolas", 11, "bold"), anchor="w",
        ).pack(side="left", padx=10, pady=6)

        close_btn = tk.Button(
            title_frame, text="X", command=toast.destroy,
            bg="#1a2a44", fg="#ff4444", relief="flat",
            font=("Consolas", 10, "bold"), width=3,
        )
        close_btn.pack(side="right", padx=4, pady=4)

        # Scrollable content
        canvas = tk.Canvas(toast, bg="#0e1225", highlightthickness=0)
        scrollbar = tk.Scrollbar(toast, orient="vertical", command=canvas.yview)
        content = tk.Frame(canvas, bg="#0e1225")

        content.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=content, anchor="nw", width=w - 20)
        canvas.configure(yscrollcommand=scrollbar.set)

        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        # Mouse wheel scrolling
        def on_mousewheel(event):
            canvas.yview_scroll(-1 * (event.delta // 120), "units")
        canvas.bind_all("<MouseWheel>", on_mousewheel)
        toast.protocol("WM_DELETE_WINDOW", lambda: (canvas.unbind_all("<MouseWheel>"), toast.destroy()))

        # Render items grouped by category
        current_cat = None
        for cat, text in items:
            if cat != current_cat:
                current_cat = cat
                icon = emoji.get(cat, "-")
                tk.Label(
                    content, text=f"{icon} {cat}",
                    bg="#0e1225", fg="#4ac0ff",
                    font=("Consolas", 10, "bold"), anchor="w",
                ).pack(fill="x", padx=8, pady=(8, 2))

            tk.Label(
                content, text=f"  {text}",
                bg="#0e1225", fg="#cccccc",
                font=("Consolas", 9), anchor="w", wraplength=440,
                justify="left",
            ).pack(fill="x", padx=8, pady=1)

        # Auto-close after 30 seconds (long enough to read)
        toast.after(30000, lambda: toast.destroy() if toast.winfo_exists() else None)

    def _show_remote_event_toast(self, event: dict):
        """Show a brief toast when a team member's event arrives."""
        who = event.get("who", "?")
        stream = event.get("stream", "?")
        summary = event.get("summary", "")[:100]

        toast = tk.Toplevel(self._tk_root)
        toast.title("AXIS — Team Activity")
        toast.attributes("-topmost", True)
        toast.overrideredirect(True)
        toast.configure(bg="#1a2a44")

        screen_w = toast.winfo_screenwidth()
        screen_h = toast.winfo_screenheight()
        toast.geometry(f"380x60+{screen_w - 400}+{screen_h - 120}")

        tk.Label(
            toast, text=f"\u25cf {who} ({stream})",
            bg="#1a2a44", fg="#4ac0ff",
            font=("Consolas", 10, "bold"), anchor="w",
        ).pack(fill="x", padx=10, pady=(6, 0))

        tk.Label(
            toast, text=summary,
            bg="#1a2a44", fg="#cccccc",
            font=("Consolas", 9), anchor="w",
        ).pack(fill="x", padx=10, pady=(0, 6))

        toast.after(8000, lambda: toast.destroy() if toast.winfo_exists() else None)

    def _show_synthesis_toast(self, summary: str):
        """Show a larger toast with the team activity synthesis."""
        toast = tk.Toplevel(self._tk_root)
        toast.title("AXIS — Team Synthesis")
        toast.attributes("-topmost", True)
        toast.resizable(True, True)

        screen_w = toast.winfo_screenwidth()
        screen_h = toast.winfo_screenheight()
        w, h = 500, 400
        toast.geometry(f"{w}x{h}+{screen_w - w - 20}+{screen_h - h - 80}")

        title_frame = tk.Frame(toast, bg="#1a2a44")
        title_frame.pack(fill="x")

        tk.Label(
            title_frame, text="\u2261 Team Activity Synthesis",
            bg="#1a2a44", fg="#4ac0ff",
            font=("Consolas", 11, "bold"), anchor="w",
        ).pack(side="left", padx=10, pady=6)

        close_btn = tk.Button(
            title_frame, text="X", command=toast.destroy,
            bg="#1a2a44", fg="#ff4444", relief="flat",
            font=("Consolas", 10, "bold"), width=3,
        )
        close_btn.pack(side="right", padx=4, pady=4)

        text_widget = tk.Text(
            toast, bg="#0e1225", fg="#cccccc",
            font=("Consolas", 9), wrap="word",
            relief="flat", padx=10, pady=10,
        )
        text_widget.pack(fill="both", expand=True)
        text_widget.insert("1.0", summary)
        text_widget.configure(state="disabled")

        toast.after(60000, lambda: toast.destroy() if toast.winfo_exists() else None)

    def _show_notification(self, notif: Notification):
        """Unified notification renderer — appearance based on priority tier."""
        config = notif.config
        color = config["color"]
        border = config["border"]
        auto_ms = config["auto_dismiss_ms"]
        w, h = config["size"]

        toast = tk.Toplevel(self._tk_root)
        toast.attributes("-topmost", True)
        toast.overrideredirect(True)

        screen_w = toast.winfo_screenwidth()
        screen_h = toast.winfo_screenheight()
        toast.geometry(f"{w}x{h}+{screen_w - w - 20}+{screen_h - h - 80}")

        if border > 0:
            # Colored border frame
            outer = tk.Frame(toast, bg=color, padx=border, pady=border)
            outer.pack(fill="both", expand=True)
            inner = tk.Frame(outer, bg="#1a1a2e")
            inner.pack(fill="both", expand=True)
        else:
            inner = tk.Frame(toast, bg="#1a1a2e")
            inner.pack(fill="both", expand=True)

        # Title
        title_color = color if notif.priority != "ambient" else "#94a3b8"
        tk.Label(
            inner, text=notif.title,
            bg="#1a1a2e", fg=title_color,
            font=("Consolas", 10, "bold"), anchor="w",
        ).pack(fill="x", padx=10, pady=(6, 0))

        # Body
        body_color = "#e2e8f0" if notif.priority != "ambient" else "#94a3b8"
        tk.Label(
            inner, text=notif.body[:120],
            bg="#1a1a2e", fg=body_color,
            font=("Consolas", 9), anchor="w", wraplength=w - 30,
            justify="left",
        ).pack(fill="x", padx=10, pady=(2, 0))

        # Details line
        if notif.details:
            tk.Label(
                inner, text=notif.details[:80],
                bg="#1a1a2e", fg="#64748b",
                font=("Consolas", 8), anchor="w",
            ).pack(fill="x", padx=10, pady=(1, 0))

        # Files
        if notif.files:
            import os as _os
            file_names = ", ".join(_os.path.basename(f) for f in notif.files[:4])
            if len(notif.files) > 4:
                file_names += f" +{len(notif.files) - 4}"
            tk.Label(
                inner, text=file_names,
                bg="#1a1a2e", fg="#818cf8",
                font=("Consolas", 8), anchor="w",
            ).pack(fill="x", padx=10, pady=(1, 0))

        # Critical: dismiss button + pulsing + beep
        if notif.priority == "critical":
            btn_frame = tk.Frame(inner, bg="#1a1a2e")
            btn_frame.pack(fill="x", padx=10, pady=(4, 6))
            tk.Button(
                btn_frame, text="Dismiss", command=toast.destroy,
                bg="#3f1e1e", fg="#ff4444", relief="flat",
                font=("Consolas", 9, "bold"),
            ).pack(side="right")

            # Pulsing border effect
            if border > 0:
                self._pulse_border(outer, color, "#660000", toast)

            # Sound
            try:
                import winsound
                winsound.Beep(1000, 200)
            except Exception:
                pass

        # Aggressive mode: beep on warnings too
        elif notif.priority == "warning" and self.settings.aggressive_alerts:
            try:
                import winsound
                winsound.Beep(800, 150)
            except Exception:
                pass

        # Click-to-dismiss (non-critical)
        if notif.priority != "critical":
            for widget in [toast, inner]:
                widget.bind("<Button-1>", lambda e: toast.destroy())

        # Auto-dismiss
        if auto_ms > 0:
            toast.after(auto_ms, lambda: toast.destroy()
                        if toast.winfo_exists() else None)

    def _pulse_border(self, frame, color_a, color_b, parent, interval=500):
        """Alternate border color for pulsing effect."""
        if not parent.winfo_exists():
            return
        current = frame.cget("bg")
        next_color = color_b if current == color_a else color_a
        frame.configure(bg=next_color)
        parent.after(interval, lambda: self._pulse_border(
            frame, color_a, color_b, parent, interval))

    def _show_blockers_panel(self):
        """Show all tracked blockers in a panel."""
        dialog = tk.Toplevel(self._tk_root)
        dialog.title("AXIS Blockers")
        dialog.attributes("-topmost", True)
        dialog.geometry("550x450")
        dialog.configure(bg="#1a1a2e")

        # Get blockers from tracker
        tracker = self.controller._blockers
        if tracker:
            open_blockers = tracker.get_open_blockers()
            stats = tracker.get_stats()
        else:
            open_blockers = []
            stats = {"open": 0, "critical": 0, "resolved": 0, "avg_age_days": 0}

        # Header
        header = (f"Open: {stats['open']} | Critical: {stats['critical']} | "
                  f"Resolved: {stats['resolved']} | Avg age: {stats['avg_age_days']}d")

        tk.Label(
            dialog, text="Blocker Tracker",
            bg="#1a1a2e", fg="#ff8800",
            font=("Consolas", 12, "bold"),
        ).pack(fill="x", padx=10, pady=(10, 2))

        tk.Label(
            dialog, text=header,
            bg="#1a1a2e", fg="#888888",
            font=("Consolas", 9),
        ).pack(fill="x", padx=10, pady=(0, 8))

        # Blocker list
        text_widget = tk.Text(
            dialog, font=("Consolas", 9),
            bg="#0a0a1e", fg="#cccccc",
            wrap="word", state="disabled",
        )
        text_widget.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        text_widget.configure(state="normal")
        if not open_blockers:
            text_widget.insert("end", "No open blockers.\n\n"
                               "Blockers are detected from conversation when someone says\n"
                               "they're blocked, waiting, or can't proceed.")
        else:
            for i, b in enumerate(open_blockers):
                text_widget.insert("end", f"{i+1}. {b.format_display()}\n\n")
        text_widget.configure(state="disabled")

    # ----- VCS alerts -----

    def _show_vcs_alert(self, insight):
        """Show a toast notification for a VCS insight."""
        type_colors = {
            "progress": "#00aa44",
            "drift": "#ccaa00",
            "stall": "#ff4444",
            "untracked": "#ff8800",
        }
        type_icons = {
            "progress": ">>",
            "drift": "<>",
            "stall": "!!",
            "untracked": "??",
        }
        color = type_colors.get(insight.type, "#666666")
        icon = type_icons.get(insight.type, "--")

        toast = tk.Toplevel(self._tk_root)
        toast.title("AXIS VCS")
        toast.attributes("-topmost", True)
        toast.overrideredirect(True)

        screen_w = toast.winfo_screenwidth()
        screen_h = toast.winfo_screenheight()
        toast.geometry(f"380x110+{screen_w - 400}+{screen_h - 170}")

        frame = tk.Frame(toast, bg=color, padx=2, pady=2)
        frame.pack(fill="both", expand=True)

        inner = tk.Frame(frame, bg="#1a1a2e")
        inner.pack(fill="both", expand=True)

        tk.Label(
            inner,
            text=f"{icon} [{insight.priority}] {insight.type.upper()}",
            bg="#1a1a2e", fg=color,
            font=("Consolas", 10, "bold"),
            anchor="w",
        ).pack(fill="x", padx=8, pady=(8, 2))

        summary = insight.summary[:90] + ("..." if len(insight.summary) > 90 else "")
        tk.Label(
            inner,
            text=summary,
            bg="#1a1a2e", fg="#cccccc",
            font=("Consolas", 9),
            anchor="w", wraplength=360,
        ).pack(fill="x", padx=8, pady=2)

        if insight.related_item:
            related = insight.related_item[:70] + ("..." if len(insight.related_item) > 70 else "")
            tk.Label(
                inner,
                text=f"[{insight.related_tag}] {related}",
                bg="#1a1a2e", fg="#00ccff",
                font=("Consolas", 8),
                anchor="w", wraplength=360,
            ).pack(fill="x", padx=8, pady=(0, 8))

        for widget in [toast, frame, inner]:
            widget.bind("<Button-1>", lambda e: toast.destroy())

        toast.after(8000, lambda: toast.destroy() if toast.winfo_exists() else None)

    # ----- Run -----

    def run(self):
        """Start the tray app (blocks on main thread)."""
        # Verify API key
        if not os.environ.get("ANTHROPIC_API_KEY"):
            print("WARNING: ANTHROPIC_API_KEY not set -- recording will fail")

        # Start tkinter thread
        self._tk_thread.start()

        # Auto-start detection if configured
        if self.settings.auto_detect:
            # Small delay to let icon appear first
            threading.Timer(1.0, self.controller.start_detecting).start()

        # Start briefing scheduler (always-on, independent of recording)
        threading.Timer(2.0, self.controller.start_briefings).start()

        # Start cloud services (Claude monitor + cloud sync, always-on)
        threading.Timer(3.0, self.controller.start_cloud_services).start()

        print("AXIS Producer tray app started")
        print("Right-click the tray icon for options")

        # Run pystray on main thread (Win32 message pump)
        self._icon.run()

        # Cleanup
        if self._tk_root:
            self._tk_root.quit()


def main():
    app = TrayApp()
    app.run()


if __name__ == "__main__":
    main()
