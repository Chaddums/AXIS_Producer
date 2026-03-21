#!/usr/bin/env python3
"""AXIS Producer — Pre-flight Verification Script.

Run this before your first real session to verify every component works.

    python verify.py           # full check
    python verify.py --quick   # skip audio/Outlook (CI-safe)
"""

import argparse
import os
import sys
import time
import threading
import queue
from datetime import datetime, timedelta

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"
WARN = "WARN"

results = []


def _safe_print(text):
    """Print with fallback for Windows console encoding issues."""
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode("ascii", errors="replace").decode())


def check(name, fn, skip_condition=False, skip_reason=""):
    if skip_condition:
        results.append((name, SKIP, skip_reason))
        _safe_print(f"  [{SKIP}] {name} -- {skip_reason}")
        return
    try:
        msg = fn()
        results.append((name, PASS, msg or ""))
        _safe_print(f"  [{PASS}] {name}" + (f" -- {msg}" if msg else ""))
    except Exception as e:
        results.append((name, FAIL, str(e)))
        _safe_print(f"  [{FAIL}] {name} -- {e}")


# ---------------------------------------------------------------------------
# 1. Imports
# ---------------------------------------------------------------------------

def check_imports():
    print("\n1. IMPORTS")

    def _core():
        from capture import AudioCapture, SAMPLE_RATE, FRAME_SIZE
        from transcriber import Transcriber
        from producer import BatchProducer, SYSTEM_PROMPT
        from digest import run_digest, parse_session_log
        from digest_db import DigestDB
        from triage import score_item, triage_session
        assert "Blockers" in SYSTEM_PROMPT, "Producer prompt missing Blockers section"
        return f"core pipeline OK, SAMPLE_RATE={SAMPLE_RATE}"

    def _tray():
        from tray_app import TrayApp
        from tray_icons import icon_idle, icon_detecting, icon_recording
        from settings import Settings
        from session_controller import SessionController, State
        icons = [icon_idle(), icon_detecting(), icon_recording()]
        assert all(i.size == (64, 64) for i in icons)
        return f"tray + 3 icons + 5 states"

    def _monitors():
        from vad_detector import VadDetector
        from loopback_capture import LoopbackCapture
        from chat_monitor import ChatMonitor
        from slack_monitor import SlackMonitor
        from email_monitor import EmailMonitor
        from vcs_monitor import VcsMonitor, GitBackend, P4VBackend
        from calendar_monitor import CalendarMonitor
        return "7 monitors"

    def _intelligence():
        from focus_advisor import FocusAdvisor
        from blocker_tracker import BlockerTracker, BlockerDB
        from deadline_scorer import adjust_item_score
        from meeting_assistant import generate_pre_meeting_brief, generate_action_sweep
        from daily_briefing import BriefingScheduler, generate_standup
        from scope_guard import ScopeGuard, RoadmapState
        return "6 intelligence modules"

    check("Core pipeline", _core)
    check("Tray app", _tray)
    check("Monitors", _monitors)
    check("Intelligence", _intelligence)


# ---------------------------------------------------------------------------
# 2. Audio devices
# ---------------------------------------------------------------------------

def check_audio(skip=False):
    print("\n2. AUDIO DEVICES")

    def _mic():
        import sounddevice as sd
        default_in = sd.default.device[0]
        dev = sd.query_devices(default_in)
        return f"{dev['name']} (idx {default_in}, {dev['default_samplerate']:.0f}Hz)"

    def _wasapi():
        import sounddevice as sd
        from loopback_capture import find_wasapi_loopback_device
        dev_idx = find_wasapi_loopback_device()
        if dev_idx is None:
            raise Exception("No WASAPI loopback device found")
        dev = sd.query_devices(dev_idx)
        return f"{dev['name']} (idx {dev_idx}, {dev['default_samplerate']:.0f}Hz)"

    def _vad():
        import webrtcvad
        import struct
        import numpy as np
        vad = webrtcvad.Vad(1)
        # Generate 30ms of silence at 16kHz
        silence = struct.pack("480h", *([0] * 480))
        result = vad.is_speech(silence, 16000)
        assert result is False, "VAD detected speech in silence"
        # Generate 30ms of loud noise
        noise = struct.pack("480h", *([20000] * 480))
        return "VAD working (silence=False)"

    check("Default mic", _mic, skip_condition=skip, skip_reason="--quick")
    check("WASAPI loopback", _wasapi, skip_condition=skip, skip_reason="--quick")
    check("WebRTC VAD", _vad)


# ---------------------------------------------------------------------------
# 3. Whisper model
# ---------------------------------------------------------------------------

def check_whisper(skip=False):
    print("\n3. WHISPER")

    def _model():
        from faster_whisper import WhisperModel
        import numpy as np
        model = WhisperModel("base.en", device="cpu", compute_type="int8")
        # Transcribe 1 second of silence — should return empty or noise
        silence = np.zeros(16000, dtype=np.float32)
        segments, _ = model.transcribe(silence, beam_size=1, language="en")
        text = " ".join(s.text for s in segments).strip()
        return f"base.en loaded, test transcription: '{text[:40]}'"

    check("Whisper model load + transcribe", _model, skip_condition=skip,
          skip_reason="--quick (slow to load)")


# ---------------------------------------------------------------------------
# 4. Claude API
# ---------------------------------------------------------------------------

def check_claude():
    print("\n4. CLAUDE API")

    def _api_key():
        key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not key:
            raise Exception("ANTHROPIC_API_KEY not set")
        return f"set ({key[:12]}...)"

    def _api_call():
        import anthropic
        client = anthropic.Anthropic()
        resp = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=50,
            messages=[{"role": "user", "content": "Say 'AXIS Producer verified' in 5 words or less."}],
        )
        return resp.content[0].text.strip()

    check("API key", _api_key)
    check("API call", _api_call)


# ---------------------------------------------------------------------------
# 5. Database
# ---------------------------------------------------------------------------

def check_database():
    print("\n5. DATABASE")

    def _db():
        from digest_db import DigestDB
        import tempfile
        path = os.path.join(tempfile.gettempdir(), "axis_verify_test.db")
        db = DigestDB(path)
        db.insert_item("2026-03-17", "10:00", "ACTION", "Game Systems",
                       "Test action item for verification", 75, "actionable")
        results = db.search("verification", limit=5)
        assert len(results) == 1, f"Expected 1 result, got {len(results)}"
        assert results[0]["tag"] == "ACTION"
        db.close()
        os.remove(path)
        return "insert + FTS5 search OK"

    def _blockers():
        from blocker_tracker import BlockerDB, Blocker
        import tempfile
        path = os.path.join(tempfile.gettempdir(), "axis_verify_test.db")
        db = BlockerDB(path)
        b = Blocker(text="Blocked on test dependency", owner="me",
                    dependency="test dep", theme="Architecture / Code",
                    source_session="2026-03-17", source_time="10:00")
        bid = db.add_blocker(b)
        assert bid > 0
        open_b = db.get_open_blockers()
        assert len(open_b) == 1
        db.resolve_blocker(bid)
        open_b = db.get_open_blockers()
        assert len(open_b) == 0
        db.close()
        os.remove(path)
        return "add + resolve + query OK"

    check("DigestDB (SQLite + FTS5)", _db)
    check("BlockerDB", _blockers)


# ---------------------------------------------------------------------------
# 6. Triage + Scoring
# ---------------------------------------------------------------------------

def check_triage():
    print("\n6. TRIAGE + SCORING")

    def _triage():
        from triage import score_item, triage_session
        item = {"tag": "ACTION", "text": "Build the sensor chain amplifier for wave 5"}
        result = score_item(item)
        assert result["score"] > 0, "Score should be positive"
        assert result["grade"] in ("actionable", "needs-context", "parked", "stale")
        return f"score={result['score']}/100 grade={result['grade']}"

    def _deadline():
        from deadline_scorer import adjust_item_score
        from calendar_monitor import CalendarEvent
        item = {"tag": "ACTION", "text": "Fix targeting before playtest",
                "theme": "Game Systems", "triage_score": 60,
                "triage_grade": "needs-context", "session_date": "2026-03-15"}
        event = CalendarEvent(subject="Playtest Review",
                              start=datetime.now() + timedelta(hours=2),
                              end=datetime.now() + timedelta(hours=3),
                              entry_id="test")
        adjusted = adjust_item_score(item, [event])
        assert adjusted["triage_score"] > 60, "Score should increase near deadline"
        delta = adjusted["triage_score"] - 60
        return f"60 -> {adjusted['triage_score']} (+{delta} deadline boost)"

    check("Triage scoring", _triage)
    check("Deadline escalation", _deadline)


# ---------------------------------------------------------------------------
# 7. Blocker detection
# ---------------------------------------------------------------------------

def check_blockers():
    print("\n7. BLOCKER DETECTION")

    def _detect():
        from blocker_tracker import detect_blockers_in_text, extract_owner_and_dependency
        results = detect_blockers_in_text(
            "I am blocked on the art assets from Adam\n"
            "The UI looks great\n"
            "This is a total blocker for the release\n"
            "Got past the compile error, we are good to go\n"
        )
        blockers = [r for r in results if not r["is_resolution"]]
        resolutions = [r for r in results if r["is_resolution"]]
        criticals = [r for r in results if r["is_critical"]]
        assert len(blockers) == 2, f"Expected 2 blockers, got {len(blockers)}"
        assert len(resolutions) == 1, f"Expected 1 resolution, got {len(resolutions)}"
        assert len(criticals) == 1, f"Expected 1 critical, got {len(criticals)}"

        owner, dep = extract_owner_and_dependency("I am blocked on the art assets from Adam")
        assert owner == "me"
        assert "art assets" in dep
        return f"2 blockers, 1 critical, 1 resolution, owner={owner}"

    check("Blocker detection", _detect)


# ---------------------------------------------------------------------------
# 8. Scope guard
# ---------------------------------------------------------------------------

def check_scope():
    print("\n8. SCOPE GUARD")

    def _find_roadmap():
        from settings import Settings
        s = Settings.load()
        for c in [s.roadmap_path,
                  os.path.join(os.path.dirname(__file__), "ALPHA_ROADMAP.md"),
                  os.path.join(os.path.dirname(__file__), "..", "..", "ALPHA_ROADMAP.md")]:
            if c and os.path.exists(os.path.normpath(c)):
                return os.path.normpath(c)
        return None

    roadmap_path = _find_roadmap()
    no_roadmap = roadmap_path is None

    def _roadmap():
        from scope_guard import RoadmapState
        roadmap = RoadmapState(roadmap_path)
        assert len(roadmap.cut_items) >= 10, f"Only {len(roadmap.cut_items)} CUT items"
        return f"{len(roadmap.cut_items)} CUT, {len(roadmap.todo_items)} TODO, " \
               f"{len(roadmap.wip_items)} WIP, {len(roadmap.done_items)} DONE"

    def _detection():
        from scope_guard import ScopeGuard
        alerts = []
        guard = ScopeGuard(roadmap_path=roadmap_path,
                           on_alert=lambda a: alerts.append(a))
        guard.check_transcript(
            "What if we also add multiplayer support?\n"
            "Let me add the relic socket system.\n"
            "The wave balance is looking good.\n"
        )
        assert len(alerts) >= 2, f"Expected 2+ alerts, got {len(alerts)}"
        types = [a.type for a in alerts]
        return f"{len(alerts)} alerts: {', '.join(types)}"

    check("Roadmap parsing", _roadmap,
          skip_condition=no_roadmap,
          skip_reason="No ALPHA_ROADMAP.md found (set roadmap_path in settings)")
    check("Scope creep + CUT detection", _detection,
          skip_condition=no_roadmap, skip_reason="No roadmap")


# ---------------------------------------------------------------------------
# 9. VCS monitor
# ---------------------------------------------------------------------------

def check_vcs():
    print("\n9. VCS MONITOR")

    def _find_repo():
        from settings import Settings
        s = Settings.load()
        for c in [s.vcs_repo_path,
                  os.path.dirname(__file__),
                  os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))]:
            if c and os.path.isdir(os.path.join(os.path.normpath(c), ".git")):
                return os.path.normpath(c)
        return None

    repo_path = _find_repo()

    def _git():
        from vcs_monitor import GitBackend
        backend = GitBackend(repo_path)
        branch = backend.current_branch()
        assert branch and branch != "unknown", "No branch detected"
        since = datetime.now() - timedelta(days=7)
        changes = backend.recent_changes(since, limit=5)
        return f"branch={branch}, {len(changes)} commits in 7d"

    check("Git backend", _git,
          skip_condition=repo_path is None,
          skip_reason="No git repo found (set vcs_repo_path in settings)")


# ---------------------------------------------------------------------------
# 10. Outlook (calendar + email)
# ---------------------------------------------------------------------------

def check_outlook(skip=False):
    print("\n10. OUTLOOK (CALENDAR + EMAIL)")

    def _com():
        import win32com.client
        import pythoncom
        pythoncom.CoInitialize()
        try:
            outlook = win32com.client.Dispatch("Outlook.Application")
            ns = outlook.GetNamespace("MAPI")
            inbox = ns.GetDefaultFolder(6)  # Inbox
            calendar = ns.GetDefaultFolder(9)  # Calendar
            return f"inbox={inbox.Items.Count} items, calendar connected"
        except Exception as e:
            err = str(e)
            if "not connected" in err.lower() or "not logged" in err.lower():
                results.append(("Outlook COM", WARN,
                                "Outlook not connected -- email/calendar monitors will auto-disable"))
                _safe_print(f"  [{WARN}] Outlook COM -- not connected "
                            "(email/calendar monitors will auto-disable at runtime)")
                return None  # handled
            raise
        finally:
            pythoncom.CoUninitialize()

    if skip:
        check("Outlook COM", lambda: None, skip_condition=True,
              skip_reason="--quick")
    else:
        try:
            result = _com()
            if result:
                results.append(("Outlook COM", PASS, result))
                _safe_print(f"  [{PASS}] Outlook COM -- {result}")
        except Exception as e:
            results.append(("Outlook COM", FAIL, str(e)))
            _safe_print(f"  [{FAIL}] Outlook COM -- {e}")


# ---------------------------------------------------------------------------
# 11. Briefing generation
# ---------------------------------------------------------------------------

def check_briefings():
    print("\n11. BRIEFINGS")

    def _standup():
        from daily_briefing import generate_standup
        from settings import Settings
        s = Settings.load()
        repo = s.vcs_repo_path or ""
        b = generate_standup(repo_path=repo)
        assert b.type == "standup"
        assert len(b.body) > 20
        return f"{len(b.body)} chars, title='{b.display_title}'"

    def _checkin():
        from daily_briefing import generate_checkin
        b = generate_checkin()
        assert b.type == "checkin"
        return f"{len(b.body)} chars"

    check("Morning standup", _standup)
    check("Midday check-in", _checkin)


# ---------------------------------------------------------------------------
# 12. Settings persistence
# ---------------------------------------------------------------------------

def check_settings():
    print("\n12. SETTINGS")

    def _roundtrip():
        from settings import Settings, SETTINGS_PATH
        import tempfile, json
        path = os.path.join(tempfile.gettempdir(), "axis_verify_settings.json")
        # Temporarily redirect
        import settings as mod
        orig = mod.SETTINGS_PATH
        mod.SETTINGS_PATH = path
        try:
            s = Settings()
            s.standup_hour = 7
            s.verbose = True
            s.save()
            s2 = Settings.load()
            assert s2.standup_hour == 7
            assert s2.verbose is True
            assert s2.auto_detect is True  # default
            return f"{len(s.__dataclass_fields__)} settings, roundtrip OK"
        finally:
            mod.SETTINGS_PATH = orig
            if os.path.exists(path):
                os.remove(path)

    check("Settings save/load", _roundtrip)


# ---------------------------------------------------------------------------
# 13. End-to-end pipeline (synthetic)
# ---------------------------------------------------------------------------

def check_e2e():
    print("\n13. END-TO-END (SYNTHETIC)")

    def _pipeline():
        """Simulate: transcript -> producer notes -> digest -> triage -> DB."""
        from digest import parse_session_log, TAG_MAP
        from triage import triage_session, load_training
        from digest_db import DigestDB
        from blocker_tracker import BlockerTracker
        import tempfile

        # Synthetic session log
        log = """# AXIS Session Log
Started: 2026-03-17 10:00

---

## [10:05] Batch 1

## Decisions Locked
- Single player only for the jam alpha
- Three starting roles confirmed: Scrapwright, Arcanist, Bruteforge

## Ideas Generated
- Corruption events mid-wave could add variety

## Action Items
- Build 2 more map layouts with different spawn positions
- Add procedural sound effects for turret firing

## Blockers
- Blocked on fog shader — needs Tron-style grid clumps, not grey squares

## Open Questions
- Should corruption events happen every run or randomly?

---
"""
        batches = parse_session_log(log)
        assert len(batches) == 1, f"Expected 1 batch, got {len(batches)}"
        assert len(batches[0]["items"]) >= 4

        # Check BLOCKER tag is picked up
        tags = [item["tag"] for item in batches[0]["items"]]
        assert "BLOCKER" in tags, f"BLOCKER tag not found in {tags}"

        # Triage
        flat = []
        for batch in batches:
            for item in batch["items"]:
                flat.append({**item, "time": batch["time"]})

        keywords = load_training()
        result = triage_session(flat, keywords)
        assert result["summary"]["total"] == len(flat)
        assert result["summary"]["avg_score"] > 0

        # DB insert + search
        db_path = os.path.join(tempfile.gettempdir(), "axis_verify_e2e.db")
        db = DigestDB(db_path)
        rows = []
        for r in result["results"]:
            rows.append({
                "session_date": "2026-03-17 10:00",
                "batch_time": "10:05",
                "tag": r["tag"],
                "theme": r["theme"],
                "text": r["text"],
                "triage_score": r["score"],
                "triage_grade": r["grade"],
            })
        db.insert_items(rows)

        # Search
        found = db.search("map layouts", limit=5)
        assert len(found) >= 1, "FTS search failed for 'map layouts'"

        actions = db.search_by_tag("ACTION", limit=10)
        assert len(actions) >= 2

        blockers_found = db.search_by_tag("BLOCKER", limit=10)
        assert len(blockers_found) >= 1, "No BLOCKER items in DB"

        db.close()
        os.remove(db_path)

        return (f"{len(batches[0]['items'])} items parsed, "
                f"avg score {result['summary']['avg_score']:.0f}/100, "
                f"DB search OK, BLOCKER tag OK")

    check("Full pipeline (parse > triage > DB > search)", _pipeline)


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def print_summary():
    print("\n" + "=" * 60)
    passes = sum(1 for _, s, _ in results if s == PASS)
    fails = sum(1 for _, s, _ in results if s == FAIL)
    skips = sum(1 for _, s, _ in results if s == SKIP)
    warns = sum(1 for _, s, _ in results if s == WARN)

    print(f"\n  RESULTS: {passes} passed, {fails} failed, "
          f"{skips} skipped, {warns} warnings")

    if warns > 0:
        print("\n  WARNINGS:")
        for name, status, msg in results:
            if status == WARN:
                print(f"    {name}: {msg}")

    if fails > 0:
        print("\n  FAILURES:")
        for name, status, msg in results:
            if status == FAIL:
                print(f"    {name}: {msg}")

    if fails == 0:
        print("\n  All checks passed. Ready for this weekend.")
    else:
        print(f"\n  Fix {fails} failure(s) before running live.")

    print()
    return fails == 0


def main():
    parser = argparse.ArgumentParser(description="AXIS Producer verification")
    parser.add_argument("--quick", action="store_true",
                        help="Skip audio device and Outlook checks")
    args = parser.parse_args()

    print("=" * 60)
    print("  AXIS Producer -- Pre-flight Verification")
    print("=" * 60)

    check_imports()
    check_audio(skip=args.quick)
    check_whisper(skip=args.quick)
    check_claude()
    check_database()
    check_triage()
    check_blockers()
    check_scope()
    check_vcs()
    check_outlook(skip=args.quick)
    check_briefings()
    check_settings()
    check_e2e()

    ok = print_summary()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
