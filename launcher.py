#!/usr/bin/env python3
"""AXIS Producer — Launcher.

Single entry point that orchestrates all services:
1. Loads .env
2. Checks prerequisites
3. Starts HTTP server for dashboard + transcribe API
4. Auto-opens dashboard in browser
5. Starts tray app (main thread)

Usage:
    python launcher.py
    python launcher.py --setup       # Re-run setup wizard
    python launcher.py --dashboard   # Open dashboard only
    python launcher.py --bot         # Run team bot only
"""

import argparse
import io
import json
import os
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse

# Add src/ to Python path so all modules can import each other
_src_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")
if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)

# Load .env before anything else
ENV_PATH = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(ENV_PATH):
    with open(ENV_PATH, "r") as f:
        for line in f:
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                key, _, val = line.partition("=")
                if key and key not in os.environ:
                    os.environ[key] = val


def check_prerequisites() -> bool:
    """Check that required config exists. Returns True if OK."""
    ok = True

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("WARNING: ANTHROPIC_API_KEY not set — voice features will not work.")
        print("  Set it with: set ANTHROPIC_API_KEY=sk-ant-...")
        print()

    from settings import Settings, SETTINGS_PATH
    if not os.path.exists(SETTINGS_PATH):
        # Create default settings — NUX will handle onboarding
        Settings().save()
        print("  First launch — NUX will open in browser.")

    return ok


class DashboardHandler(SimpleHTTPRequestHandler):
    """HTTP handler that serves static files + /api/transcribe endpoint."""

    whisper_model = "base.en"  # class-level, set by factory
    _static_dir = None  # class-level, set by start_http_server

    def __init__(self, *args, **kwargs):
        # Serve from static/ directory regardless of cwd
        directory = self._static_dir or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "static")
        super().__init__(*args, directory=directory, **kwargs)

    _phone_mic = None  # class-level, set by launcher

    def do_GET(self):
        path = urlparse(self.path).path

        # First-launch redirect: no auth token → NUX
        if path in ("/", "/index.html", "/dashboard.html"):
            from settings import Settings
            s = Settings.load()
            if not s.auth_token:
                self.send_response(302)
                self.send_header("Location", "/nux.html")
                self.end_headers()
                return

        if path == "/api/version":
            import hashlib
            dashboard_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "static", "dashboard.html")
            try:
                with open(dashboard_path, "rb") as f:
                    h = hashlib.md5(f.read()).hexdigest()[:12]
                self._json_response({"version": h})
            except Exception:
                self._json_response({"version": "unknown"})
            return
        elif path == "/api/config":
            from settings import Settings
            from dataclasses import asdict
            s = Settings.load()
            cfg = asdict(s)
            cfg["has_auth"] = bool(s.auth_token)
            cfg["panel_labels"] = s.get_panel_labels()
            self._json_response(cfg)
            return
        elif path == "/api/phone-qr":
            if self._phone_mic:
                self._json_response(self._phone_mic.get_status())
            else:
                self._json_response({"error": "Phone mic not available"}, 503)
            return
        elif path == "/api/phone-status":
            if self._phone_mic:
                self._json_response({
                    "paired": self._phone_mic.is_paired,
                    "streaming": self._phone_mic.is_streaming,
                })
            else:
                self._json_response({"paired": False, "streaming": False})
            return
        elif path == "/api/briefing":
            self._handle_briefing()
            return
        elif path == "/api/terms":
            self._handle_terms_get()
            return
        elif path == "/api/feedback/stats":
            self._handle_feedback_stats()
            return
        # Fall through to static file serving
        super().do_GET()

    def do_POST(self):
        path = urlparse(self.path).path

        if path == "/api/transcribe":
            self._handle_transcribe()
        elif path == "/api/chat":
            self._handle_chat()
        elif path == "/api/settings":
            self._handle_settings_update()
        elif path == "/api/feedback":
            self._handle_feedback()
        elif path == "/api/terms":
            self._handle_terms_post()
        elif path == "/api/terms/import":
            self._handle_terms_import()
        else:
            self.send_error(404, "Not Found")

    def do_DELETE(self):
        path = urlparse(self.path).path

        if path == "/api/terms":
            self._handle_terms_delete()
        else:
            self.send_error(404, "Not Found")

    def do_OPTIONS(self):
        """Handle CORS preflight."""
        self.send_response(200)
        self._cors_headers()
        self.end_headers()

    def end_headers(self):
        """Add CORS headers to all responses."""
        self._cors_headers()
        super().end_headers()

    def _cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    # Common Whisper hallucinations on silence
    _HALLUCINATIONS = {
        "you", "thank you", "thanks for watching", "bye", "the end",
        "thanks", "thank you for watching", "thanks for listening",
        "subscribe", "like and subscribe", "so", "okay", "um",
    }

    @staticmethod
    def _audio_is_silent(path: str, threshold: float = 200.0) -> bool:
        """Check if audio file is effectively silent by RMS energy."""
        try:
            import numpy as np
            import sounddevice  # noqa — ensures audio libs available
            # Read raw audio bytes — works for WAV and WebM via faster_whisper's decoder
            from faster_whisper.audio import decode_audio
            audio = decode_audio(path)
            rms = np.sqrt(np.mean(audio ** 2))
            return rms < threshold / 32768.0  # normalized threshold
        except Exception:
            return False

    def _handle_transcribe(self):
        """Receive audio, transcribe with Whisper, return text."""
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            audio_data = self.rfile.read(content_length)

            if not audio_data:
                self._json_response({"error": "No audio data"}, 400)
                return

            # Save to temp file
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                f.write(audio_data)
                temp_path = f.name

            try:
                # Gate: skip transcription if audio is silence
                if self._audio_is_silent(temp_path):
                    self._json_response({"text": ""})
                    return

                from faster_whisper import WhisperModel
                model = WhisperModel(self.whisper_model or "base.en",
                                     device="cpu", compute_type="int8")
                segments, _ = model.transcribe(temp_path)
                text = " ".join(s.text.strip() for s in segments)

                # Filter known hallucinations
                if text.strip().lower() in self._HALLUCINATIONS:
                    text = ""
            finally:
                os.unlink(temp_path)

            self._json_response({"text": text})

        except Exception as e:
            self._json_response({"error": str(e)}, 500)

    def _handle_chat(self):
        """Receive a chat message and post via backend API."""
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(content_length))

            from settings import Settings
            settings = Settings.load()

            event = {
                "who": body.get("who", settings.user_identity),
                "stream": body.get("stream", "chat"),
                "session_id": "",
                "event_type": body.get("event_type", "chat"),
                "area": None,
                "files": [],
                "summary": body.get("text", ""),
                "raw": body.get("raw", {}),
                "project": body.get("project"),
                "parent_id": body.get("parent_id"),
            }

            if not settings.backend_url:
                self._json_response({"error": "No backend URL configured. Run setup first."}, 503)
                return
            if not settings.auth_token:
                self._json_response({"error": "Not logged in. Run setup to sign in."}, 401)
                return
            if not settings.team_id:
                self._json_response({"error": "No team configured. Run setup to create a team."}, 400)
                return

            from backend_client import BackendClient
            client = BackendClient(settings.backend_url, settings.auth_token)
            event["team_id"] = settings.team_id
            count = client.push_events([event])
            if count > 0:
                self._json_response({"ok": True})
            else:
                self._json_response({"error": "Backend rejected the message. Check your connection and auth."}, 502)

        except Exception as e:
            self._json_response({"error": str(e)}, 500)

    def _handle_briefing(self):
        """Generate a topic-clustered team briefing from recent events."""
        try:
            from settings import Settings
            settings = Settings.load()

            if not settings.backend_url or not settings.auth_token or not settings.team_id:
                self._json_response({"error": "Not configured"}, 503)
                return

            from backend_client import BackendClient
            client = BackendClient(settings.backend_url, settings.auth_token)

            # Pull recent batch events directly using event_type filter
            import httpx
            resp = httpx.get(
                f"{settings.backend_url}/events",
                params={
                    "team_id": settings.team_id,
                    "event_type": "session_batch,chat,voice_chat,insight",
                    "order": "desc",
                    "limit": "200",
                },
                headers={"Authorization": f"Bearer {settings.auth_token}"},
                timeout=30.0,
            )
            events = resp.json() if resp.status_code == 200 else []

            if not events:
                self._json_response({"topics": [], "needs_action": [], "conflicts": [], "people": {}})
                return

            from topic_synthesis import synthesize_events, score_relevance

            # Use the user's configured LLM or fall back to env key
            api_key = settings.llm_api_key or os.environ.get("ANTHROPIC_API_KEY", "")
            briefing = synthesize_events(
                events,
                provider=settings.llm_provider if settings.llm_api_key else "anthropic",
                api_key=api_key,
                model="claude-haiku-4-5-20251001",  # fast + cheap for synthesis
                ollama_url=settings.ollama_url,
            )

            self._json_response(briefing)

        except Exception as e:
            self._json_response({"error": str(e)}, 500)

    def _handle_settings_update(self):
        """Accept partial settings updates from NUX/dashboard."""
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(content_length))

            from settings import Settings
            s = Settings.load()
            allowed = set(s.__dataclass_fields__.keys())
            for key, value in body.items():
                if key in allowed:
                    setattr(s, key, value)
            s.save()
            self._json_response({"ok": True})
        except Exception as e:
            self._json_response({"error": str(e)}, 500)

    def _handle_feedback(self):
        """Record a user feedback action (dismiss/resolve/follow/backlog)."""
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(content_length))

            action = body.get("action", "")
            text = body.get("text", "")
            if not action or not text:
                self._json_response({"error": "action and text required"}, 400)
                return

            from user_db import UserDB
            db = UserDB()
            db.record_feedback(action, text, body.get("tag", ""), body.get("theme", ""))
            db.close()
            self._json_response({"ok": True})
        except Exception as e:
            self._json_response({"error": str(e)}, 500)

    def _handle_feedback_stats(self):
        """Return feedback action counts."""
        try:
            from user_db import UserDB
            db = UserDB()
            stats = db.get_feedback_stats()
            signals = db.get_term_signals()
            db.close()
            # Return top 20 signals by absolute weight
            top_signals = sorted(signals.items(), key=lambda x: abs(x[1]), reverse=True)[:20]
            self._json_response({
                "action_counts": stats,
                "top_signals": [{"term": t, "weight": w} for t, w in top_signals],
            })
        except Exception as e:
            self._json_response({"error": str(e)}, 500)

    def _handle_terms_get(self):
        """Return all user-defined terms."""
        try:
            from user_db import UserDB
            db = UserDB()
            terms = db.get_all_terms()
            db.close()
            self._json_response({"terms": terms})
        except Exception as e:
            self._json_response({"error": str(e)}, 500)

    def _handle_terms_post(self):
        """Add or update a user-defined term."""
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(content_length))

            term = body.get("term", "").strip()
            if not term:
                self._json_response({"error": "term required"}, 400)
                return

            from user_db import UserDB
            db = UserDB()
            db.set_term(
                term,
                int(body.get("weight", 0)),
                body.get("theme", ""),
                body.get("notes", ""),
            )
            db.close()
            self._json_response({"ok": True})
        except Exception as e:
            self._json_response({"error": str(e)}, 500)

    def _handle_terms_delete(self):
        """Remove a user-defined term."""
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(content_length))

            term = body.get("term", "").strip()
            if not term:
                self._json_response({"error": "term required"}, 400)
                return

            from user_db import UserDB
            db = UserDB()
            deleted = db.delete_term(term)
            db.close()
            self._json_response({"ok": True, "deleted": deleted})
        except Exception as e:
            self._json_response({"error": str(e)}, 500)

    def _handle_terms_import(self):
        """Bulk import terms from JSON array."""
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(content_length))

            terms_list = body.get("terms", [])
            if not terms_list:
                self._json_response({"error": "terms array required"}, 400)
                return

            from user_db import UserDB
            db = UserDB()
            count = db.import_terms(terms_list)
            db.close()
            self._json_response({"ok": True, "count": count})
        except Exception as e:
            self._json_response({"error": str(e)}, 500)

    def _json_response(self, data: dict, status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def log_message(self, format, *args):
        """Suppress default access logs."""
        pass


def check_for_updates() -> bool:
    """Check if remote has new commits, auto-pull if so. Returns True if updated."""
    project_dir = os.path.dirname(os.path.abspath(__file__))
    try:
        subprocess.run(
            ["git", "fetch", "--quiet"],
            cwd=project_dir, capture_output=True, timeout=15,
        )
        result = subprocess.run(
            ["git", "rev-list", "--count", "HEAD..@{upstream}"],
            cwd=project_dir, capture_output=True, text=True, timeout=10,
        )
        behind = int(result.stdout.strip()) if result.stdout.strip() else 0

        if behind > 0:
            # Show what's coming
            log_result = subprocess.run(
                ["git", "log", "--oneline", "HEAD..@{upstream}", "--max-count=5"],
                cwd=project_dir, capture_output=True, text=True, timeout=10,
            )
            commits = log_result.stdout.strip()
            print(f"  Update available: {behind} new commit(s)")
            if commits:
                for line in commits.split("\n"):
                    print(f"    {line}")

            # Pull
            pull = subprocess.run(
                ["git", "pull", "--ff-only"],
                cwd=project_dir, capture_output=True, text=True, timeout=30,
            )
            if pull.returncode == 0:
                print("  Updated successfully.")
                return True
            else:
                print(f"  Auto-pull failed: {pull.stderr.strip()}")
                print("  You may need to pull manually.")
                return False
        return False
    except Exception as e:
        # Non-fatal — just can't check for updates
        return False


def start_update_checker(interval: float = 300.0):
    """Background thread that checks for updates periodically."""
    project_dir = os.path.dirname(os.path.abspath(__file__))

    def _check_loop():
        while True:
            time.sleep(interval)
            try:
                subprocess.run(
                    ["git", "fetch", "--quiet"],
                    cwd=project_dir, capture_output=True, timeout=15,
                )
                result = subprocess.run(
                    ["git", "rev-list", "--count", "HEAD..@{upstream}"],
                    cwd=project_dir, capture_output=True, text=True, timeout=10,
                )
                behind = int(result.stdout.strip()) if result.stdout.strip() else 0

                if behind > 0:
                    log_result = subprocess.run(
                        ["git", "log", "--oneline", "HEAD..@{upstream}",
                         "--max-count=3"],
                        cwd=project_dir, capture_output=True, text=True,
                        timeout=10,
                    )
                    commits = log_result.stdout.strip()
                    print(f"\n  [updater] {behind} update(s) available:")
                    if commits:
                        for line in commits.split("\n"):
                            print(f"    {line}")

                    # Auto-pull
                    pull = subprocess.run(
                        ["git", "pull", "--ff-only"],
                        cwd=project_dir, capture_output=True, text=True,
                        timeout=30,
                    )
                    if pull.returncode == 0:
                        print("  [updater] Updated. Restart to apply changes.")
                        # Could auto-restart here, but safer to notify
                    else:
                        print(f"  [updater] Pull failed: {pull.stderr.strip()}")
            except Exception:
                pass

    t = threading.Thread(target=_check_loop, name="update-checker", daemon=True)
    t.start()
    return t


def start_http_server(port: int = 8080, whisper_model: str = "base.en",
                      https_port: int = 8443):
    """Start the dashboard HTTP server in a background thread.

    Serves on 0.0.0.0 so phones on the LAN can reach phone_mic.html.
    Also starts an HTTPS server on https_port for getUserMedia on mobile.
    """
    project_dir = os.path.dirname(os.path.abspath(__file__))
    static_dir = os.path.join(project_dir, "static")
    if not os.path.isdir(static_dir):
        static_dir = project_dir

    DashboardHandler._static_dir = static_dir
    DashboardHandler.whisper_model = whisper_model

    try:
        server = HTTPServer(("0.0.0.0", port), DashboardHandler)
    except OSError as e:
        if "Address already in use" in str(e) or "10048" in str(e):
            print(f"  Dashboard server: port {port} in use, trying {port + 1}")
            server = HTTPServer(("0.0.0.0", port + 1), DashboardHandler)
            port = port + 1
        else:
            raise

    thread = threading.Thread(target=server.serve_forever,
                              name="dashboard-http", daemon=True)
    thread.start()
    print(f"  Dashboard: http://localhost:{port}/dashboard.html")

    # HTTPS server for phone mic (getUserMedia requires secure context)
    https_server = None
    try:
        from phone_mic_server import generate_self_signed_cert
        cert_path = os.path.join(project_dir, "phone_mic_cert.pem")
        key_path = os.path.join(project_dir, "phone_mic_key.pem")
        if not os.path.exists(cert_path) or not os.path.exists(key_path):
            generate_self_signed_cert(cert_path, key_path)

        import ssl
        https_server = HTTPServer(("0.0.0.0", https_port), DashboardHandler)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert_path, key_path)
        https_server.socket = ctx.wrap_socket(https_server.socket, server_side=True)

        https_thread = threading.Thread(target=https_server.serve_forever,
                                        name="dashboard-https", daemon=True)
        https_thread.start()
        from phone_mic_server import get_local_ip
        local_ip = get_local_ip()
        print(f"  Phone HTTPS: https://{local_ip}:{https_port}/phone_mic.html")
    except Exception as e:
        print(f"  HTTPS server failed: {e} (phone mic will not work)")

    return server, port


def main():
    parser = argparse.ArgumentParser(description="AXIS Producer Launcher")
    parser.add_argument("--setup", action="store_true",
                        help="Re-run setup wizard")
    parser.add_argument("--dashboard", action="store_true",
                        help="Open dashboard only (no tray app)")
    parser.add_argument("--bot", action="store_true",
                        help="Run team bot only")
    parser.add_argument("--no-browser", action="store_true",
                        help="Don't auto-open dashboard in browser")
    args = parser.parse_args()

    if args.setup:
        import setup
        setup.main()
        return

    if args.bot:
        import team_bot
        team_bot.main()
        return

    if not check_prerequisites():
        sys.exit(1)

    # Load settings
    from settings import Settings
    settings = Settings.load()

    print()
    print("  AXIS Producer starting...")
    print()

    # Check for updates on startup
    updated = check_for_updates()
    if updated:
        print("  Code updated — restarting...")
        os.execv(sys.executable, [sys.executable] + sys.argv)

    # Check for updates every 5 minutes in background
    start_update_checker(interval=300.0)

    # Start phone mic server (WebSocket for phone-as-mic pairing)
    try:
        from phone_mic_server import PhoneMicServer
        _phone_stop = threading.Event()
        phone_mic = PhoneMicServer(
            chunk_queue=None,  # will be set when recording starts
            stop_event=_phone_stop,
            verbose=settings.verbose,
        )
        phone_mic.start()
        DashboardHandler._phone_mic = phone_mic
    except Exception as e:
        print(f"  Phone mic server failed: {e}")
        phone_mic = None

    # Start HTTP server
    server, port = start_http_server(
        port=settings.dashboard_port,
        whisper_model=settings.whisper_model,
    )

    # Auto-open dashboard
    if not args.no_browser and not args.dashboard:
        threading.Timer(1.5, lambda: webbrowser.open(
            f"http://localhost:{port}/dashboard.html")).start()

    if args.dashboard:
        # Dashboard-only mode
        webbrowser.open(f"http://localhost:{port}/dashboard.html")
        print("  Press Ctrl+C to stop.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        server.shutdown()
        return

    # Start tray app (blocks on main thread) with crash recovery
    log_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "axis_crash.log")
    max_restarts = 3
    restart_count = 0

    while restart_count <= max_restarts:
        try:
            from tray_app import TrayApp
            app = TrayApp()
            app.run()
            break  # clean exit
        except KeyboardInterrupt:
            break
        except Exception as e:
            restart_count += 1
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            crash_msg = f"[{timestamp}] CRASH #{restart_count}: {type(e).__name__}: {e}\n"

            # Log to file
            try:
                import traceback
                with open(log_file, "a", encoding="utf-8") as f:
                    f.write(crash_msg)
                    traceback.print_exc(file=f)
                    f.write("\n")
            except Exception:
                pass

            print(f"\n  AXIS Producer crashed: {e}")

            if restart_count <= max_restarts:
                wait = restart_count * 5
                print(f"  Restarting in {wait}s... (attempt {restart_count}/{max_restarts})")
                print(f"  Crash log: {log_file}")
                time.sleep(wait)
            else:
                print(f"  Too many crashes. Check {log_file}")
                break

    server.shutdown()
    print("  AXIS Producer stopped.")


if __name__ == "__main__":
    main()
