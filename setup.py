#!/usr/bin/env python3
"""AXIS Producer — Interactive Setup Wizard.

Guides the user through configuring AXIS Producer with opt-in features,
audio device selection, API key setup, and cloud sync verification.

Usage:
    python setup.py
"""

import json
import os
import subprocess
import sys

SETTINGS_PATH = os.path.join(os.path.dirname(__file__), "tray_settings.json")
ENV_PATH = os.path.join(os.path.dirname(__file__), ".env")

# Pre-configured Supabase (shared team DB)
SUPABASE_URL = "https://vktcojdvracuzwzeqisw.supabase.co"
SUPABASE_KEY = "sb_publishable_NAeW_rDUSS-oiM03p2JD5Q_IWWKPAmK"


def banner():
    print()
    print("  ========================================")
    print("   AXIS Producer — Setup")
    print("  ========================================")
    print()


def ask(prompt: str, default: str = "") -> str:
    """Prompt user for input with an optional default."""
    if default:
        result = input(f"  {prompt} [{default}]: ").strip()
        return result if result else default
    return input(f"  {prompt}: ").strip()


def ask_yn(prompt: str, default_yes: bool = True) -> bool:
    """Yes/no prompt."""
    hint = "Y/n" if default_yes else "y/N"
    result = input(f"  [{hint}] {prompt} ").strip().lower()
    if not result:
        return default_yes
    return result in ("y", "yes")


def step_identity() -> str:
    """Step 1: Get user identity."""
    print("  [1/7] Who are you?")
    print()
    name = ask("Your name (e.g. adam, stu)")
    if not name:
        print("  ERROR: Name is required.")
        sys.exit(1)
    print(f"  Hello, {name}!")
    print()
    return name


def step_install_deps():
    """Step 2: Install Python dependencies."""
    print("  [2/7] Installing dependencies...")
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", "requirements.txt",
             "--quiet", "--disable-pip-version-check"],
            check=True, cwd=os.path.dirname(__file__) or ".",
        )
        print("  Done.")
    except subprocess.CalledProcessError:
        print("  WARNING: Some dependencies may have failed to install.")
        print("  You can retry manually: pip install -r requirements.txt")
    print()


def step_features() -> dict:
    """Step 3: Choose which features to enable."""
    print("  [3/7] Which features do you want to enable?")
    print("        (You can change these later in tray_settings.json)")
    print()

    features = {}

    # Defaults-on features
    features["mic"] = ask_yn("Microphone capture (voice-to-producer)", True)
    features["loopback"] = ask_yn("System audio capture (loopback)", True)
    features["claude_monitor"] = ask_yn("Claude Code monitor (watch your CC sessions)", True)
    features["cloud_sync"] = ask_yn("Cloud sync (shared team DB)", True)
    features["chat_monitor"] = ask_yn("Clipboard chat monitor", True)
    features["vcs_monitor"] = ask_yn("Git activity monitor", True)

    print()
    # Defaults-off features
    features["slack_monitor"] = ask_yn("Slack monitor", False)
    features["email_monitor"] = ask_yn("Email monitor (Outlook)", False)
    features["calendar_monitor"] = ask_yn("Calendar monitor (Outlook)", False)
    features["daily_briefings"] = ask_yn("Daily briefings (standup/checkin/wrapup)", False)

    print()
    return features


def step_audio(features: dict) -> tuple[int | None, int | None]:
    """Step 4: Audio device selection."""
    mic_device = None
    loopback_device = None

    if not features.get("mic") and not features.get("loopback"):
        print("  [4/7] Audio devices: skipped (no audio features enabled)")
        print()
        return mic_device, loopback_device

    print("  [4/7] Audio device selection")
    print()

    try:
        import sounddevice as sd
        devices = sd.query_devices()
        default_in, default_out = sd.default.device

        if features.get("mic"):
            print("  Available input devices:")
            input_devs = []
            for i, d in enumerate(devices):
                if d["max_input_channels"] > 0:
                    marker = " <-- default" if i == default_in else ""
                    print(f"    [{i}] {d['name']}{marker}")
                    input_devs.append(i)

            choice = ask("Pick mic device", str(default_in))
            try:
                mic_device = int(choice)
            except ValueError:
                mic_device = default_in
            print()

        if features.get("loopback"):
            print("  Available output devices (for loopback):")
            for i, d in enumerate(devices):
                if d["max_output_channels"] > 0:
                    marker = " <-- default" if i == default_out else ""
                    print(f"    [{i}] {d['name']}{marker}")

            choice = ask("Pick loopback device", str(default_out))
            try:
                loopback_device = int(choice)
            except ValueError:
                loopback_device = default_out
            print()

    except ImportError:
        print("  sounddevice not installed yet — using system defaults.")
        print("  Run setup again after install to pick devices.")
        print()

    return mic_device, loopback_device


def step_api_keys() -> str:
    """Step 5: Check/set API keys."""
    print("  [5/7] API Keys")
    print()

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")

    if api_key:
        print(f"  ANTHROPIC_API_KEY: found ({api_key[:12]}...)")
    else:
        print("  ANTHROPIC_API_KEY: not set")
        key_input = ask("Enter your Anthropic API key (or press Enter to skip)")
        if key_input:
            api_key = key_input
            # Save to .env
            env_lines = []
            if os.path.exists(ENV_PATH):
                with open(ENV_PATH, "r") as f:
                    env_lines = [l for l in f.readlines()
                                 if not l.startswith("ANTHROPIC_API_KEY=")]
            env_lines.append(f"ANTHROPIC_API_KEY={api_key}\n")
            with open(ENV_PATH, "w") as f:
                f.writelines(env_lines)
            os.environ["ANTHROPIC_API_KEY"] = api_key
            print("  Saved to .env")
        else:
            print("  Skipped — you'll need this for voice transcription.")
            print("  Set it later: set ANTHROPIC_API_KEY=sk-ant-...")

    print()
    return api_key


def step_cloud_sync(features: dict):
    """Step 6: Test cloud sync connection."""
    print("  [6/7] Cloud sync")
    print()

    if not features.get("cloud_sync"):
        print("  Cloud sync: disabled (skipped)")
        print()
        return

    print(f"  Supabase URL: {SUPABASE_URL[:40]}...")

    try:
        from cloud_db import CloudDB
        db = CloudDB(SUPABASE_URL, SUPABASE_KEY)
        if db.connected:
            # Quick test
            test = db.insert_event({
                "ts": "2026-01-01T00:00:00Z",
                "who": "setup_test",
                "stream": "test",
                "session_id": "",
                "event_type": "test",
                "area": None,
                "files": [],
                "summary": "setup connection test",
                "raw": {},
                "project": None,
            })
            if test:
                # Clean up
                db._client.table("events").delete().eq("who", "setup_test").execute()
                print("  Connection test: passed")
            else:
                print("  Connection test: insert failed (check Supabase config)")
        else:
            print("  Connection: failed (Supabase not reachable)")
    except Exception as e:
        print(f"  Connection test: error ({e})")

    print()


def step_write_config(name: str, features: dict,
                      mic_device, loopback_device):
    """Step 7: Write tray_settings.json."""
    print("  [7/7] Writing configuration...")
    print()

    settings = {
        "mic_device": mic_device,
        "loopback_device": loopback_device,
        "whisper_model": "base.en",
        "batch_interval": 300,
        "log_dir": ".",
        "auto_detect": features.get("mic", True),
        "vad_sensitivity": 1,
        "chat_monitor": features.get("chat_monitor", True),
        "slack_monitor": features.get("slack_monitor", False),
        "slack_channel_ids": [],
        "slack_poll_interval": 15.0,
        "email_monitor": features.get("email_monitor", False),
        "email_poll_interval": 30.0,
        "email_unread_only": True,
        "focus_advisor": True,
        "vcs_monitor": features.get("vcs_monitor", True),
        "vcs_repo_path": None,
        "vcs_poll_interval": 120.0,
        "calendar_monitor": features.get("calendar_monitor", False),
        "calendar_poll_interval": 60.0,
        "pre_meeting_minutes": 10,
        "roadmap_path": None,
        "daily_briefings": features.get("daily_briefings", False),
        "standup_hour": 9,
        "checkin_hour": 13,
        "wrapup_hour": 17,
        "nag_interval_hours": 4,
        "cloud_sync": features.get("cloud_sync", True),
        "supabase_url": SUPABASE_URL,
        "supabase_key": SUPABASE_KEY,
        "user_identity": name,
        "synthesis_interval": 900,
        "claude_monitor": features.get("claude_monitor", True),
        "claude_project_paths": ["*"],  # watch all CC projects (narrow later if needed)
        "claude_poll_interval": 3.0,
        "notification_level": "info",
        "aggressive_alerts": False,
        "dashboard_port": 8080,
        "verbose": True,
    }

    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)

    print(f"  tray_settings.json written")

    # Also ensure .env has Supabase creds
    env_lines = []
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, "r") as f:
            env_lines = f.readlines()

    env_keys = {l.split("=")[0] for l in env_lines if "=" in l}
    additions = []
    if "SUPABASE_URL" not in env_keys:
        additions.append(f"SUPABASE_URL={SUPABASE_URL}\n")
    if "SUPABASE_KEY" not in env_keys:
        additions.append(f"SUPABASE_KEY={SUPABASE_KEY}\n")

    if additions:
        with open(ENV_PATH, "a") as f:
            f.writelines(additions)

    print()


def step_create_shortcut():
    """Create a Desktop shortcut for AXIS Producer."""
    try:
        import win32com.client
        desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        shortcut_path = os.path.join(desktop, "AXIS Producer.lnk")

        bat_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "AXIS Producer.bat")

        shell = win32com.client.Dispatch("WScript.Shell")
        shortcut = shell.CreateShortCut(shortcut_path)
        shortcut.Targetpath = bat_path
        shortcut.WorkingDirectory = os.path.dirname(bat_path)
        shortcut.Description = "AXIS Producer — Team Awareness Tool"
        shortcut.save()

        print(f"  Desktop shortcut created: {shortcut_path}")
    except ImportError:
        print("  Desktop shortcut: skipped (pywin32 not available)")
        print("  You can create one manually pointing to 'AXIS Producer.bat'")
    except Exception as e:
        print(f"  Desktop shortcut: failed ({e})")

    print()


def main():
    banner()

    name = step_identity()
    step_install_deps()
    features = step_features()
    mic_device, loopback_device = step_audio(features)
    step_api_keys()
    step_cloud_sync(features)
    step_write_config(name, features, mic_device, loopback_device)
    step_create_shortcut()

    print("  ========================================")
    print("   Setup complete!")
    print("  ========================================")
    print()
    print("  To start AXIS Producer:")
    print('    Double-click "AXIS Producer" on your Desktop')
    print("    Or run: python launcher.py")
    print()
    print("  Dashboard: http://localhost:8080/dashboard.html")
    print()


if __name__ == "__main__":
    main()
