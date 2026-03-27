"""Session Controller — state machine orchestrating all AXIS Producer components.

States: IDLE → DETECTING → PROMPTED → RECORDING → STOPPING → IDLE

Manages thread lifecycle for mic capture, loopback capture, transcriber,
batch producer, and chat monitor.
"""

import enum
import os
import queue
import threading
import time
from datetime import datetime

from capture import AudioCapture
from loopback_capture import LoopbackCapture
from transcriber import Transcriber
from producer import BatchProducer
from chat_monitor import ChatMonitor
from slack_monitor import SlackMonitor
from email_monitor import EmailMonitor
from focus_advisor import FocusAdvisor
from vcs_monitor import VcsMonitor, GitBackend, VcsInsight
from calendar_monitor import CalendarMonitor, CalendarEvent
from meeting_assistant import generate_pre_meeting_brief, generate_action_sweep
from blocker_tracker import BlockerTracker, Blocker
from daily_briefing import BriefingScheduler, Briefing
from scope_guard import ScopeGuard, ScopeAlert
from vad_detector import VadDetector
from claude_monitor import ClaudeMonitor, ClaudeEvent
from cloud_sync import CloudSync
from git_health import GitHealthMonitor, GitHealthAlert, BranchInfo
from phone_mic_server import PhoneMicServer
from backend_client import BackendClient
from settings import Settings


class State(enum.Enum):
    IDLE = "idle"
    DETECTING = "detecting"
    PROMPTED = "prompted"
    RECORDING = "recording"
    STOPPING = "stopping"


class SessionController:
    """Orchestrates the full AXIS Producer pipeline."""

    def __init__(self, settings: Settings,
                 on_state_change=None,
                 on_speech_detected=None,
                 on_focus_match=None,
                 on_vcs_insight=None,
                 on_meeting_approaching=None,
                 on_meeting_ended=None,
                 on_brief_ready=None,
                 on_sweep_ready=None,
                 on_blocker=None,
                 on_briefing=None,
                 on_scope_alert=None,
                 on_items_logged=None,
                 on_remote_event=None,
                 on_claude_event=None,
                 on_synthesis=None):
        self.settings = settings
        self.on_state_change = on_state_change        # callback(State)
        self.on_speech_detected = on_speech_detected  # callback() — show popup
        self.on_focus_match = on_focus_match          # callback(FocusMatch)
        self.on_vcs_insight = on_vcs_insight          # callback(VcsInsight)
        self.on_meeting_approaching = on_meeting_approaching  # callback(event, brief_text)
        self.on_meeting_ended = on_meeting_ended              # callback(event)
        self.on_brief_ready = on_brief_ready                  # callback(brief_text)
        self.on_sweep_ready = on_sweep_ready                  # callback(sweep_text)
        self.on_blocker = on_blocker                          # callback(type, blocker)
        self.on_briefing = on_briefing                        # callback(Briefing)
        self.on_scope_alert = on_scope_alert                  # callback(ScopeAlert)
        self.on_items_logged = on_items_logged                # callback(list[(category, text)])
        self.on_remote_event = on_remote_event                # callback(dict) — other user's event
        self.on_claude_event = on_claude_event                # callback(ClaudeEvent)
        self.on_synthesis = on_synthesis                       # callback(str) — team summary

        self._state = State.IDLE
        self._lock = threading.Lock()

        # VAD detector (lightweight, always-on during DETECTING)
        self._detector: VadDetector | None = None

        # Focus advisor (cross-refs messages with digest DB)
        self._focus: FocusAdvisor | None = None

        # Calendar monitor
        self._calendar: CalendarMonitor | None = None

        # Blocker tracker
        self._blockers: BlockerTracker | None = None

        # Scope guard
        self._scope: ScopeGuard | None = None

        # Briefing scheduler (always-on, independent of recording state)
        self._briefing_scheduler: BriefingScheduler | None = None
        self._briefing_stop: threading.Event | None = None
        self._briefing_thread: threading.Thread | None = None

        # Backend client (authenticated API access)
        self._backend_client: BackendClient | None = None
        if settings.backend_url and settings.auth_token:
            self._backend_client = BackendClient(
                settings.backend_url, settings.auth_token,
                verbose=settings.verbose,
            )

        # Claude monitor + cloud sync (can run independently of recording)
        self._claude_monitor: ClaudeMonitor | None = None
        self._cloud_sync: CloudSync | None = None
        self._cloud_threads: list[threading.Thread] = []
        self._cloud_stop: threading.Event | None = None

        # Recording pipeline state
        self._stop_event: threading.Event | None = None
        self._threads: list[threading.Thread] = []
        self._producer: BatchProducer | None = None

        # Stats
        self.batch_count = 0
        self.item_count = 0
        self._recording_start: datetime | None = None

    @property
    def state(self) -> State:
        return self._state

    def _set_state(self, new_state: State):
        self._state = new_state
        if self.on_state_change:
            try:
                self.on_state_change(new_state)
            except Exception:
                pass

    # ----- Briefing scheduler (always-on) -----

    def start_briefings(self):
        """Start the briefing scheduler (runs regardless of recording state)."""
        if self._briefing_thread and self._briefing_thread.is_alive():
            return
        if not self.settings.daily_briefings:
            return

        self._briefing_stop = threading.Event()

        def _get_calendar():
            if self._calendar:
                return self._calendar.upcoming_events
            return []

        repo_path = self.settings.vcs_repo_path or os.path.abspath("../..")

        self._briefing_scheduler = BriefingScheduler(
            self._briefing_stop,
            on_briefing=self._handle_briefing,
            standup_hour=self.settings.standup_hour,
            checkin_hour=self.settings.checkin_hour,
            wrapup_hour=self.settings.wrapup_hour,
            nag_interval_hours=self.settings.nag_interval_hours,
            repo_path=repo_path,
            calendar_events_fn=_get_calendar,
            verbose=self.settings.verbose,
        )
        self._briefing_thread = threading.Thread(
            target=self._briefing_scheduler.run,
            name="briefing-scheduler", daemon=True,
        )
        self._briefing_thread.start()

    def stop_briefings(self):
        """Stop the briefing scheduler."""
        if self._briefing_stop:
            self._briefing_stop.set()
        if self._briefing_thread:
            self._briefing_thread.join(timeout=3.0)
            self._briefing_thread = None

    def _handle_briefing(self, briefing: Briefing):
        """Called by BriefingScheduler when a briefing is ready."""
        if self.on_briefing:
            try:
                self.on_briefing(briefing)
            except Exception:
                pass

    # ----- Cloud services (always-on, independent of recording) -----

    def start_cloud_services(self):
        """Start Claude monitor and cloud sync (runs regardless of recording state)."""
        if self._cloud_stop and not self._cloud_stop.is_set():
            return  # already running
        self._cloud_stop = threading.Event()

        verbose = self.settings.verbose

        # Claude Code conversation monitor
        if self.settings.claude_monitor:
            self._claude_monitor = ClaudeMonitor(
                self._cloud_stop,
                on_event=self._handle_claude_event,
                project_paths=self.settings.claude_project_paths or None,
                poll_interval=self.settings.claude_poll_interval,
                verbose=verbose,
            )
            t = threading.Thread(target=self._claude_monitor.run,
                                 name="claude-monitor", daemon=True)
            self._cloud_threads.append(t)
            t.start()

        # Cloud sync — prefer backend API, fall back to direct Supabase
        can_backend = self._backend_client and self.settings.team_id
        can_legacy = (self.settings.supabase_url and self.settings.supabase_key)
        if self.settings.cloud_sync and (can_backend or can_legacy):
            self._cloud_sync = CloudSync(
                self._cloud_stop,
                on_remote_event=self._handle_remote_event,
                on_synthesis=self._handle_synthesis,
                backend_client=self._backend_client if can_backend else None,
                team_id=self.settings.team_id if can_backend else "",
                supabase_url=self.settings.supabase_url,
                supabase_key=self.settings.supabase_key,
                user_identity=self.settings.user_identity,
                synthesis_interval=self.settings.synthesis_interval,
                verbose=verbose,
            )
            t = threading.Thread(target=self._cloud_sync.run,
                                 name="cloud-sync", daemon=True)
            self._cloud_threads.append(t)
            t.start()

        # Git health monitor (push/pull reminders + branch visibility)
        repo_path = self.settings.vcs_repo_path or os.path.abspath("../..")
        self._git_health = GitHealthMonitor(
            self._cloud_stop,
            repo_path=repo_path,
            on_alert=self._handle_git_alert,
            on_branches=self._handle_branches,
            poll_interval=self.settings.vcs_poll_interval,
            verbose=verbose,
        )
        t = threading.Thread(target=self._git_health.run,
                             name="git-health", daemon=True)
        self._cloud_threads.append(t)
        t.start()

    def stop_cloud_services(self):
        """Stop Claude monitor and cloud sync."""
        if self._cloud_stop:
            self._cloud_stop.set()
        for t in self._cloud_threads:
            t.join(timeout=5.0)
        self._cloud_threads.clear()
        self._claude_monitor = None
        self._cloud_sync = None

    def _handle_git_alert(self, alert: GitHealthAlert):
        """Called by GitHealthMonitor on push/pull/divergence alerts."""
        if self._cloud_sync:
            self._cloud_sync.push_event({
                "ts": datetime.now().astimezone().isoformat(),
                "who": self.settings.user_identity,
                "stream": "git_health",
                "session_id": "",
                "event_type": alert.alert_type,
                "area": None,
                "files": [],
                "summary": alert.message,
                "raw": {"severity": alert.severity, "branch": alert.branch,
                        **alert.details},
                "project": None,
            })
        # Surface as VCS insight to tray
        if self.on_vcs_insight:
            try:
                self.on_vcs_insight(VcsInsight(
                    type=alert.alert_type,
                    summary=alert.message,
                    details=str(alert.details),
                    files=[],
                ))
            except Exception:
                pass

    def _handle_branches(self, branches: list[BranchInfo]):
        """Called by GitHealthMonitor with full branch state."""
        if not self._cloud_sync:
            return
        for b in branches:
            if b.ahead_of_main == 0 and not b.is_current:
                continue  # skip inactive branches with no new work
            self._cloud_sync.push_event({
                "ts": b.last_commit_time or datetime.now().astimezone().isoformat(),
                "who": self.settings.user_identity,
                "stream": "git_branch",
                "session_id": "",
                "event_type": "branch_status",
                "area": b.name,
                "files": b.recent_files[:10],
                "summary": (f"{'*' if b.is_current else ''}{b.name}: "
                            f"{b.last_commit_msg[:60]} "
                            f"(+{b.ahead_of_main}/-{b.behind_main}, "
                            f"{b.unpushed} unpushed)"),
                "raw": {
                    "branch": b.name,
                    "is_current": b.is_current,
                    "author": b.last_commit_author,
                    "ahead": b.ahead_of_main,
                    "behind": b.behind_main,
                    "unpushed": b.unpushed,
                    "unpulled": b.unpulled,
                },
                "project": None,
            })

    def _handle_claude_event(self, event: ClaudeEvent):
        """Called by ClaudeMonitor on new Claude Code activity."""
        if self._cloud_sync:
            self._cloud_sync.push_claude_event(event)
        if self.on_claude_event:
            try:
                self.on_claude_event(event)
            except Exception:
                pass

    def _handle_remote_event(self, event: dict):
        """Called by CloudSync when another user's event arrives."""
        if self.on_remote_event:
            try:
                self.on_remote_event(event)
            except Exception:
                pass

    def _handle_synthesis(self, summary: str):
        """Called by CloudSync when a new team synthesis is generated."""
        if self.on_synthesis:
            try:
                self.on_synthesis(summary)
            except Exception:
                pass

    # ----- Detection phase -----

    def start_detecting(self):
        """Start the lightweight VAD detector."""
        with self._lock:
            if self._state not in (State.IDLE, State.STOPPING):
                return
            self._set_state(State.DETECTING)

        self._detector = VadDetector(
            on_speech_detected=self._handle_speech,
            device=self.settings.mic_device,
            sensitivity=self.settings.vad_sensitivity,
            verbose=self.settings.verbose,
        )
        self._detector.start()

    def stop_detecting(self):
        """Stop the VAD detector, return to idle."""
        with self._lock:
            if self._state != State.DETECTING:
                return
        if self._detector:
            self._detector.stop()
            self._detector = None
        self._set_state(State.IDLE)

    def _handle_speech(self):
        """Called by VadDetector when speech is detected."""
        with self._lock:
            if self._state != State.DETECTING:
                return
            self._set_state(State.PROMPTED)

        if self.on_speech_detected:
            self.on_speech_detected()

    # ----- User response to prompt -----

    def on_prompt_response(self, accepted: bool):
        """Called when user responds to the 'Start recording?' prompt."""
        with self._lock:
            if self._state != State.PROMPTED:
                return

        if accepted:
            if self._detector:
                self._detector.on_accepted()
            self.start_recording()
        else:
            # Resume detecting with escalating cooldown
            self._set_state(State.DETECTING)
            if self._detector:
                self._detector.on_declined()

    # ----- Recording phase -----

    def start_recording(self):
        """Start the full recording pipeline (mic + loopback + transcriber + producer + chat)."""
        with self._lock:
            if self._state == State.RECORDING:
                return
            self._set_state(State.RECORDING)

        # Stop detector if running
        if self._detector:
            self._detector.stop()
            self._detector = None

        self._recording_start = datetime.now()
        self.batch_count = 0
        self.item_count = 0

        # Shared state
        self._stop_event = threading.Event()
        chunk_queue = queue.Queue()
        buffer_lock = threading.Lock()
        transcript_buffer: list[str] = []

        # Ensure log directory exists
        log_dir = os.path.abspath(self.settings.log_dir)
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, "session_log.md")

        verbose = self.settings.verbose

        # Mic capture
        mic = AudioCapture(chunk_queue, self._stop_event,
                           device=self.settings.mic_device, verbose=verbose)

        # Loopback capture
        loopback = LoopbackCapture(chunk_queue, self._stop_event,
                                   device=self.settings.loopback_device,
                                   verbose=verbose)

        # Transcriber
        transcriber = Transcriber(chunk_queue, self._stop_event,
                                  buffer_lock, transcript_buffer,
                                  model_name=self.settings.whisper_model,
                                  verbose=verbose)

        # Producer — wrap callback to also push to cloud
        def _items_logged_wrapper(items):
            if self.on_items_logged:
                self.on_items_logged(items)
            if self._cloud_sync:
                self._cloud_sync.push_voice_batch(items)

        self._producer = BatchProducer(
            self._stop_event, buffer_lock, transcript_buffer,
            log_path=log_path,
            interval=self.settings.batch_interval,
            verbose=verbose,
            on_items_logged=_items_logged_wrapper,
            workspace_context=self.settings.workspace_context,
            output_terminology=self.settings.output_terminology,
            backend_client=self._backend_client,
            team_id=self.settings.team_id,
            llm_provider=self.settings.llm_provider,
            llm_api_key=self.settings.llm_api_key,
            llm_model=self.settings.llm_model,
            ollama_url=self.settings.ollama_url,
        )

        # Phone mic (WebSocket, shares chunk_queue with local mic)
        self._phone_mic = PhoneMicServer(
            chunk_queue, self._stop_event, verbose=verbose)
        self._phone_mic.start()

        # Start threads
        self._threads = [
            threading.Thread(target=mic.run, name="mic-capture", daemon=True),
            threading.Thread(target=loopback.run, name="loopback-capture", daemon=True),
            threading.Thread(target=transcriber.run, name="transcriber", daemon=True),
            threading.Thread(target=self._producer.run, name="producer", daemon=True),
        ]

        # Chat monitor (optional)
        if self.settings.chat_monitor:
            chat = ChatMonitor(self._stop_event, buffer_lock, transcript_buffer,
                               verbose=verbose)
            self._threads.append(
                threading.Thread(target=chat.run, name="chat-monitor", daemon=True)
            )

        # Focus advisor — cross-references incoming messages with digest DB
        if self.settings.focus_advisor:
            self._focus = FocusAdvisor(
                on_focus_match=self._handle_focus_match,
                verbose=verbose,
            )
        else:
            self._focus = None

        # Blocker tracker — detects and tracks blockers from conversation
        self._blockers = BlockerTracker(
            on_new_blocker=lambda b: self._handle_blocker("new", b),
            on_blocker_escalated=lambda b: self._handle_blocker("escalated", b),
            on_blocker_resolved=lambda b: self._handle_blocker("resolved", b),
            verbose=verbose,
        )

        # Scope guard — catches scope creep and overcommitment
        roadmap_path = self.settings.roadmap_path or os.path.join(
            os.path.abspath(self.settings.log_dir), "..", "ALPHA_ROADMAP.md")
        repo_path = self.settings.vcs_repo_path or os.path.abspath("../..")
        self._scope = ScopeGuard(
            roadmap_path=roadmap_path,
            on_alert=self._handle_scope_alert,
            repo_path=repo_path,
            verbose=verbose,
        )

        # Slack monitor (optional)
        if self.settings.slack_monitor:
            slack = SlackMonitor(
                self._stop_event,
                on_message=self._handle_incoming_message,
                channel_ids=self.settings.slack_channel_ids or None,
                poll_interval=self.settings.slack_poll_interval,
                verbose=verbose,
            )
            self._threads.append(
                threading.Thread(target=slack.run, name="slack-monitor", daemon=True)
            )

        # Email monitor (optional)
        if self.settings.email_monitor:
            email = EmailMonitor(
                self._stop_event,
                on_message=self._handle_incoming_message,
                poll_interval=self.settings.email_poll_interval,
                unread_only=self.settings.email_unread_only,
                verbose=verbose,
            )
            self._threads.append(
                threading.Thread(target=email.run, name="email-monitor", daemon=True)
            )

        # VCS monitor — tracks commits against action items
        if self.settings.vcs_monitor:
            repo_path = self.settings.vcs_repo_path or os.path.abspath("../..")
            backend = GitBackend(repo_path)
            self._vcs = VcsMonitor(
                self._stop_event,
                backend=backend,
                on_insight=self._handle_vcs_insight,
                poll_interval=self.settings.vcs_poll_interval,
                verbose=verbose,
            )
            self._threads.append(
                threading.Thread(target=self._vcs.run, name="vcs-monitor", daemon=True)
            )
        else:
            self._vcs = None

        # Calendar monitor — tracks meetings, fires pre/post callbacks
        if self.settings.calendar_monitor:
            self._calendar = CalendarMonitor(
                self._stop_event,
                on_meeting_approaching=self._handle_meeting_approaching,
                on_meeting_started=self._handle_meeting_started,
                on_meeting_ended=self._handle_meeting_ended,
                poll_interval=self.settings.calendar_poll_interval,
                pre_meeting_minutes=self.settings.pre_meeting_minutes,
                verbose=verbose,
            )
            self._threads.append(
                threading.Thread(target=self._calendar.run, name="calendar-monitor",
                                 daemon=True)
            )
        else:
            self._calendar = None

        for t in self._threads:
            t.start()

        if verbose:
            print("  [controller] recording started")

    def stop_recording(self):
        """Stop recording, flush final batch, run digest."""
        with self._lock:
            if self._state != State.RECORDING:
                return
            self._set_state(State.STOPPING)

        if self.settings.verbose:
            print("  [controller] stopping -- flushing final batch...")

        # Signal all threads to stop
        if self._stop_event:
            self._stop_event.set()

        # Wait for threads
        for t in self._threads:
            t.join(timeout=5.0)
        self._threads.clear()

        # Stop phone mic server
        if hasattr(self, '_phone_mic') and self._phone_mic:
            self._phone_mic.stop()
            self._phone_mic = None

        # Capture batch count
        if self._producer:
            self.batch_count = self._producer._batch_count
            self._producer = None

        # Run digest post-processor
        log_path = os.path.join(os.path.abspath(self.settings.log_dir),
                                "session_log.md")
        self._run_digest(log_path)

        # Resume detecting
        if self.settings.auto_detect:
            self.start_detecting()
        else:
            self._set_state(State.IDLE)

    def _run_digest(self, log_path: str):
        """Run the session digest post-processor (non-blocking)."""
        try:
            from digest import run_digest, DEFAULT_OUTPUT
            if self.settings.verbose:
                print("  [controller] running session digest...")
            run_digest(log_path=log_path, output_path=DEFAULT_OUTPUT,
                       verbose=self.settings.verbose)
        except Exception as e:
            print(f"  [controller] digest failed (non-fatal): {e}")

    # ----- Incoming message handling (Slack/email → focus advisor) -----

    def _handle_incoming_message(self, source: str, sender: str,
                                 text: str, timestamp: str):
        """Called by SlackMonitor or EmailMonitor when a new message arrives."""
        if self._focus:
            self._focus.check_message(source, sender, text, timestamp)
        if self._blockers:
            self._blockers.check_incoming_message(source, text)
        if self._cloud_sync:
            self._cloud_sync.push_message_event(source, sender, text, timestamp)

    def _handle_focus_match(self, match):
        """Called by FocusAdvisor when a high-priority match is found."""
        if self.on_focus_match:
            try:
                self.on_focus_match(match)
            except Exception:
                pass

    def _handle_vcs_insight(self, insight: VcsInsight):
        """Called by VcsMonitor when it detects progress/drift/stall."""
        if self._cloud_sync:
            self._cloud_sync.push_git_event(
                event_type=insight.type,
                summary=insight.summary,
                files=getattr(insight, "files", []),
                raw={"details": getattr(insight, "details", "")},
            )
        if self.on_vcs_insight:
            try:
                self.on_vcs_insight(insight)
            except Exception:
                pass

    def _handle_blocker(self, event_type: str, blocker: Blocker):
        """Called by BlockerTracker on new/escalated/resolved blockers."""
        if self.on_blocker:
            try:
                self.on_blocker(event_type, blocker)
            except Exception:
                pass

    def _handle_scope_alert(self, alert: ScopeAlert):
        """Called by ScopeGuard on scope creep or overcommitment."""
        if self.on_scope_alert:
            try:
                self.on_scope_alert(alert)
            except Exception:
                pass

    # ----- Meeting lifecycle (calendar → briefs/sweeps) -----

    def _handle_meeting_approaching(self, event: CalendarEvent, minutes_until: float):
        """Called when a meeting is approaching. Generate and surface a brief."""
        def _generate():
            calendar_events = self._calendar.upcoming_events if self._calendar else []
            brief = generate_pre_meeting_brief(
                event, calendar_events=calendar_events,
                verbose=self.settings.verbose,
            )
            if self.on_meeting_approaching:
                self.on_meeting_approaching(event, brief)
            if self.on_brief_ready:
                self.on_brief_ready(brief)

        # Run in background thread to avoid blocking the calendar monitor
        threading.Thread(target=_generate, name="brief-gen", daemon=True).start()

    def _handle_meeting_started(self, event: CalendarEvent):
        """Called when a meeting starts. Could auto-start recording."""
        if self.settings.verbose:
            print(f"  [controller] meeting started: {event.subject}")

    def _handle_meeting_ended(self, event: CalendarEvent):
        """Called when a meeting ends. Generate action sweep if recording was active."""
        if self.on_meeting_ended:
            try:
                self.on_meeting_ended(event)
            except Exception:
                pass

        # If we were recording, generate an action sweep
        if self._state == State.RECORDING or self._state == State.STOPPING:
            def _sweep():
                log_path = os.path.join(
                    os.path.abspath(self.settings.log_dir), "session_log.md")
                sweep = generate_action_sweep(
                    log_path, meeting_name=event.subject,
                    verbose=self.settings.verbose,
                )
                if self.on_sweep_ready:
                    self.on_sweep_ready(sweep)

            threading.Thread(target=_sweep, name="sweep-gen", daemon=True).start()

    def force_batch(self):
        """Force an immediate batch send."""
        if self._producer and self._state == State.RECORDING:
            self._producer.force_batch.set()

    # ----- Status -----

    def status_text(self) -> str:
        """Return a human-readable status string for the tooltip."""
        if self._state == State.IDLE:
            return "AXIS Producer — idle"
        elif self._state == State.DETECTING:
            return "AXIS Producer — listening for speech"
        elif self._state == State.PROMPTED:
            return "AXIS Producer — conversation detected"
        elif self._state == State.RECORDING:
            elapsed = ""
            if self._recording_start:
                delta = datetime.now() - self._recording_start
                mins = int(delta.total_seconds() // 60)
                elapsed = f", {mins}m"
            batches = self._producer._batch_count if self._producer else 0
            extras = []
            if self._focus and self._focus.stats["matches_found"] > 0:
                extras.append(f"{self._focus.stats['matches_found']} focus")
            if self._vcs and self._vcs.insights_generated > 0:
                extras.append(f"{self._vcs.insights_generated} vcs")
            extra_str = ", " + "/".join(extras) if extras else ""
            return f"AXIS Producer — recording ({batches} batches{elapsed}{extra_str})"
        elif self._state == State.STOPPING:
            return "AXIS Producer — stopping..."
        return "AXIS Producer"
