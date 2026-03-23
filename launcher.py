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
import sys
import threading
import time
import webbrowser
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse

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

    settings_path = os.path.join(os.path.dirname(__file__), "tray_settings.json")
    if not os.path.exists(settings_path):
        print("ERROR: No tray_settings.json found.")
        print("  Run setup first: python setup.py")
        return False

    return ok


class DashboardHandler(SimpleHTTPRequestHandler):
    """HTTP handler that serves static files + /api/transcribe endpoint."""

    whisper_model = "base.en"  # class-level, set by factory

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/version":
            # Return hash of dashboard.html so clients can detect changes
            import hashlib
            dashboard_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "dashboard.html")
            try:
                with open(dashboard_path, "rb") as f:
                    h = hashlib.md5(f.read()).hexdigest()[:12]
                self._json_response({"version": h})
            except Exception:
                self._json_response({"version": "unknown"})
            return
        # Fall through to static file serving
        super().do_GET()

    def do_POST(self):
        path = urlparse(self.path).path

        if path == "/api/transcribe":
            self._handle_transcribe()
        elif path == "/api/chat":
            self._handle_chat()
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
                from faster_whisper import WhisperModel
                model = WhisperModel(self.whisper_model or "base.en",
                                     device="cpu", compute_type="int8")
                segments, _ = model.transcribe(temp_path)
                text = " ".join(s.text.strip() for s in segments)
            finally:
                os.unlink(temp_path)

            self._json_response({"text": text})

        except Exception as e:
            self._json_response({"error": str(e)}, 500)

    def _handle_chat(self):
        """Receive a chat message and post to Supabase."""
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(content_length))

            from cloud_db import CloudDB
            from settings import Settings
            settings = Settings.load()

            db = CloudDB(settings.supabase_url, settings.supabase_key)
            result = db.insert_event({
                "ts": body.get("ts", ""),
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
            })

            self._json_response({"ok": True, "event": result})

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


def start_http_server(port: int = 8080, whisper_model: str = "base.en"):
    """Start the dashboard HTTP server in a background thread."""
    project_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(project_dir)

    DashboardHandler.whisper_model = whisper_model

    try:
        server = HTTPServer(("127.0.0.1", port), DashboardHandler)
    except OSError as e:
        if "Address already in use" in str(e) or "10048" in str(e):
            print(f"  Dashboard server: port {port} in use, trying {port + 1}")
            server = HTTPServer(("127.0.0.1", port + 1), DashboardHandler)
            port = port + 1
        else:
            raise

    thread = threading.Thread(target=server.serve_forever,
                              name="dashboard-http", daemon=True)
    thread.start()

    print(f"  Dashboard: http://localhost:{port}/dashboard.html")
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

    # Start tray app (blocks on main thread)
    try:
        from tray_app import TrayApp
        app = TrayApp()
        app.run()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        print("  AXIS Producer stopped.")


if __name__ == "__main__":
    main()
