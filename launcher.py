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
