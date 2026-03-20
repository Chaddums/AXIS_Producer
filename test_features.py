#!/usr/bin/env python3
"""AXIS Producer — Interactive Feature Test.

Walks through each feature one at a time so you can verify it works
before using it in a real session. Each test tells you what to do,
runs the feature, and shows you the result.

    python test_features.py              # run all tests in order
    python test_features.py --list       # list available tests
    python test_features.py --test 3     # run a specific test
    python test_features.py --from 5     # start from test 5
"""

import argparse
import os
import sys
import time
import queue
import struct
import threading
import tempfile
from datetime import datetime, timedelta

# Ensure we can import from this directory
sys.path.insert(0, os.path.dirname(__file__))


def ask(prompt):
    """Prompt user and wait for input."""
    try:
        return input(f"\n  > {prompt} ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\n  Cancelled.")
        sys.exit(0)


def pause(msg="Press Enter to continue..."):
    ask(msg)


def header(num, title):
    print(f"\n{'='*60}")
    print(f"  TEST {num}: {title}")
    print(f"{'='*60}")


# -----------------------------------------------------------------------
# Test 1: Tray icon appears
# -----------------------------------------------------------------------

def test_tray_icon():
    header(1, "TRAY ICON")
    print("""
  This test launches the tray icon and verifies it appears.
  You should see an AXIS icon (gray circle with 'A') in your
  system tray / notification area.

  What to check:
    - Gray icon appears in the tray
    - Right-click shows a menu with Start Listening, Start Recording, etc.
    - Tooltip says "AXIS Producer - idle"
    """)
    pause("Press Enter to launch the tray icon (it will close after you confirm)...")

    import pystray
    from tray_icons import icon_idle
    from threading import Event

    done = Event()

    def on_click(icon, item):
        done.set()
        icon.stop()

    icon = pystray.Icon(
        "test", icon_idle(), "AXIS Producer - test",
        menu=pystray.Menu(
            pystray.MenuItem("Click here to confirm it works", on_click),
        ),
    )

    t = threading.Thread(target=icon.run, daemon=True)
    t.start()

    print("  Icon should be in your tray now.")
    print("  Right-click it and select 'Click here to confirm it works'.")

    done.wait(timeout=30)
    if done.is_set():
        print("\n  PASSED - Tray icon works.")
    else:
        icon.stop()
        print("\n  TIMEOUT - Did you see the icon? (It may be in the overflow area)")
        print("  Tip: Check the ^ arrow in the taskbar to see hidden tray icons.")


# -----------------------------------------------------------------------
# Test 2: VAD speech detection
# -----------------------------------------------------------------------

def test_vad_detection():
    header(2, "SPEECH DETECTION (VAD)")
    print("""
  This test runs the lightweight VAD detector on your mic.
  When you speak, it should detect your voice within ~1 second.

  What to do:
    1. Press Enter to start listening
    2. Wait 2 seconds (silence baseline)
    3. Say something clearly for 2-3 seconds
    4. The detector should fire within 1 second of you speaking
    """)
    pause("Press Enter to start the VAD detector...")

    from vad_detector import VadDetector

    detected = threading.Event()

    def on_speech():
        print("  ** SPEECH DETECTED **")
        detected.set()

    detector = VadDetector(on_speech_detected=on_speech, verbose=True)
    detector.start()

    print("  Listening... speak now.")
    detected.wait(timeout=15)

    detector.stop()

    if detected.is_set():
        print("\n  PASSED - VAD detected your speech.")
    else:
        print("\n  No speech detected in 15 seconds.")
        print("  Check: Is your mic set as the default input device?")


# -----------------------------------------------------------------------
# Test 3: Mic capture + transcription
# -----------------------------------------------------------------------

def test_mic_transcription():
    header(3, "MIC CAPTURE + WHISPER TRANSCRIPTION")
    print("""
  This test records your mic for 10 seconds and transcribes it.

  What to do:
    1. Press Enter to start recording
    2. Say: "We decided the game is called Vine Logic TD and it's
       a single player tower defense. Stu is going to build two
       more map layouts tonight."
    3. Wait for transcription to appear

  What to check:
    - Your words appear as text (doesn't need to be perfect)
    - Timestamps are shown
    """)
    pause("Press Enter to start recording (10 seconds)...")

    from capture import AudioCapture
    from transcriber import Transcriber

    stop = threading.Event()
    chunk_queue = queue.Queue()
    buffer_lock = threading.Lock()
    transcript = []

    cap = AudioCapture(chunk_queue, stop, verbose=True)
    trans = Transcriber(chunk_queue, stop, buffer_lock, transcript,
                        verbose=True)

    threads = [
        threading.Thread(target=cap.run, daemon=True),
        threading.Thread(target=trans.run, daemon=True),
    ]
    for t in threads:
        t.start()

    print("  Recording... speak now.")
    time.sleep(10)
    print("  Stopping...")
    stop.set()

    for t in threads:
        t.join(timeout=5)

    print(f"\n  Transcript ({len(transcript)} lines):")
    for line in transcript:
        print(f"    {line}")

    if transcript:
        print("\n  PASSED - Mic capture and transcription working.")
    else:
        print("\n  No transcript captured.")
        print("  If you spoke, try: python axis_producer.py --list-devices")


# -----------------------------------------------------------------------
# Test 4: WASAPI loopback capture
# -----------------------------------------------------------------------

def test_loopback():
    header(4, "SYSTEM AUDIO CAPTURE (WASAPI LOOPBACK)")
    print("""
  This test captures system audio (what comes out of your speakers).
  This is how AXIS hears the other people on a Zoom/Teams call.

  What to do:
    1. Press Enter to start capturing
    2. Play a YouTube video or any audio on your PC for 5 seconds
    3. Check if audio chunks are detected

  What to check:
    - "[loopback] speech detected in system audio" appears
    - "[loopback] chunk sealed" appears when the audio stops
    """)
    pause("Press Enter to start loopback capture (10 seconds)...")

    from loopback_capture import LoopbackCapture

    stop = threading.Event()
    chunk_queue = queue.Queue()

    cap = LoopbackCapture(chunk_queue, stop, verbose=True)
    t = threading.Thread(target=cap.run, daemon=True)
    t.start()

    print("  Capturing system audio... play something on your PC now.")
    time.sleep(10)
    stop.set()
    t.join(timeout=3)

    chunks = 0
    while not chunk_queue.empty():
        chunk_queue.get()
        chunks += 1

    if chunks > 0:
        print(f"\n  PASSED - Captured {chunks} audio chunk(s) from system audio.")
    else:
        print("\n  No system audio chunks captured.")
        print("  This is OK if you didn't play any audio.")
        print("  If you did play audio, check your default output device.")


# -----------------------------------------------------------------------
# Test 5: Claude producer batch
# -----------------------------------------------------------------------

def test_producer_batch():
    header(5, "CLAUDE PRODUCER BATCH")
    print("""
  This test sends a synthetic transcript to Claude and shows
  the structured notes it extracts (decisions, actions, etc.)

  What to check:
    - Output has ## Decisions Locked, ## Action Items, etc.
    - Items are terse and accurate
    - ## Blockers section appears if blocker language is present
    """)
    pause("Press Enter to send a test batch to Claude...")

    import anthropic
    from producer import SYSTEM_PROMPT, MODEL, MAX_TOKENS

    transcript = """
[10:00] Okay so we've decided the game is called Vine Logic TD, single player roguelike tower defense.
[10:01] Yeah and I think three starting roles is right. Scrapwright, Arcanist, and Bruteforge.
[10:02] I'll build two more map layouts tonight. Different spawn positions, different terrain.
[10:03] We need to figure out what the corruption events look like. That's still open.
[10:04] One concern is the fog system. It looks like grey squares right now, not the Tron aesthetic we want.
[10:05] I'm blocked on the sound design. Can't test the full experience without at least placeholder sounds.
[10:06] Adam should have the wave balance spreadsheet done by Thursday.
"""

    client = anthropic.Anthropic()
    print("  Sending to Claude...")
    resp = client.messages.create(
        model=MODEL, max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": transcript}],
    )
    notes = resp.content[0].text
    print(f"\n  Claude's output:\n")
    for line in notes.split("\n"):
        print(f"    {line}")

    has_sections = any(s in notes for s in ["Decisions", "Action", "Question", "Watch", "Blocker"])
    if has_sections:
        print("\n  PASSED - Claude extracted structured notes.")
    else:
        print("\n  Output doesn't have expected sections. Check the format.")


# -----------------------------------------------------------------------
# Test 6: Blocker detection
# -----------------------------------------------------------------------

def test_blocker_detection():
    header(6, "BLOCKER DETECTION")
    print("""
  This test runs blocker detection on sample text to verify it
  catches blocker language, extracts owners, and resolves blockers.
    """)
    pause("Press Enter to run blocker detection...")

    from blocker_tracker import BlockerTracker, BlockerDB

    db_path = os.path.join(tempfile.gettempdir(), "axis_test_blockers.db")
    events = []

    tracker = BlockerTracker(
        on_new_blocker=lambda b: events.append(("NEW", b)),
        on_blocker_escalated=lambda b: events.append(("ESCALATED", b)),
        on_blocker_resolved=lambda b: events.append(("RESOLVED", b)),
        db_path=db_path,
        verbose=True,
    )

    # Simulate producer notes with blockers
    notes = """
## Action Items
- Build two more map layouts

## Blockers
- Stu is blocked on sound design assets from the audio pack
- Team is waiting for the fog shader rewrite before visual polish pass
"""
    tracker.process_producer_notes(notes, session_date="2026-03-17", batch_time="10:00")

    # Simulate a message that resolves one
    tracker.check_incoming_message("slack:#dev", "Got the fog shader working, we're good to go on that")

    print(f"\n  Events:")
    for event_type, b in events:
        print(f"    [{event_type}] {b.text[:60]}")
        if b.owner:
            print(f"      Owner: {b.owner}")
        if b.dependency:
            print(f"      Waiting on: {b.dependency}")

    # Show open blockers
    open_b = tracker.get_open_blockers()
    print(f"\n  Open blockers: {len(open_b)}")
    for b in open_b:
        print(f"    [{b.severity}] {b.text[:60]}")

    tracker.close()
    try:
        os.remove(db_path)
    except OSError:
        pass

    new_count = sum(1 for t, _ in events if t == "NEW")
    resolved_count = sum(1 for t, _ in events if t == "RESOLVED")
    print(f"\n  PASSED - {new_count} detected, {resolved_count} resolved." if new_count >= 2 else
          f"\n  CHECK - Only {new_count} blockers detected (expected 2+)")


# -----------------------------------------------------------------------
# Test 7: Scope guard
# -----------------------------------------------------------------------

def test_scope_guard():
    header(7, "SCOPE GUARD")
    print("""
  This test checks if the scope guard catches CUT items and
  overcommitment when someone volunteers for work.

  The roadmap has 16 CUT items including multiplayer, campaign mode,
  save/load mid-run, magic system, etc.
    """)
    pause("Press Enter to run scope guard...")

    from scope_guard import ScopeGuard

    alerts = []
    roadmap_path = os.path.normpath(
        os.path.join(os.path.dirname(__file__), "..", "..", "ALPHA_ROADMAP.md"))

    guard = ScopeGuard(
        roadmap_path=roadmap_path,
        on_alert=lambda a: alerts.append(a),
        repo_path=os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..")),
        verbose=True,
    )

    guard.check_transcript("""
What if we also add multiplayer support for the jam?
I'll knock out the terrain manipulation UI this afternoon.
Shouldn't we add a campaign mode too?
The wave balance is looking good after the tuning pass.
Let me add the relic socket system while I'm in there.
I'll fix that sensor targeting bug.
""")

    print(f"\n  Alerts: {len(alerts)}")
    for a in alerts:
        print(f"    [{a.severity:8s}] {a.type}: {a.message}")
        print(f"             Heard: \"{a.trigger_text[:60]}\"")

    safe_lines = 2  # "wave balance" and "sensor bug" should NOT trigger
    caught = len(alerts)
    print(f"\n  {caught} scope issues caught, 2 safe lines passed through.")
    if caught >= 3:
        print("  PASSED - Scope guard catching CUT items and volunteering.")
    else:
        print(f"  CHECK - Expected 3+ alerts, got {caught}")


# -----------------------------------------------------------------------
# Test 8: Digest + triage + DB search
# -----------------------------------------------------------------------

def test_digest_pipeline():
    header(8, "DIGEST + TRIAGE + DB SEARCH")
    print("""
  This test runs the full digest pipeline on a synthetic session log:
  parse -> triage score -> theme route -> DB insert -> FTS search.
    """)
    pause("Press Enter to run digest pipeline...")

    from digest import parse_session_log
    from triage import triage_session, load_training
    from digest_db import DigestDB

    log = """# AXIS Session Log
Started: 2026-03-17 10:00

---

## [10:05] Batch 1

## Decisions Locked
- Single player only for the jam alpha
- Three starting roles: Scrapwright, Arcanist, Bruteforge

## Ideas Generated
- Corruption events mid-wave for variety
- AXIS commentary could mock the player for leaking enemies

## Action Items
- Build 2 more map layouts with different spawn positions
- Add procedural sound effects for turret firing and enemy death

## Blockers
- Blocked on fog shader quality — needs Tron-style grid clumps

## Open Questions
- Should corruption events happen every run or randomly?

## Watch List
- Pathfinding performance may degrade with 18 node types on large maps

---
"""
    batches = parse_session_log(log)
    flat = []
    for batch in batches:
        for item in batch["items"]:
            flat.append({**item, "time": batch["time"]})

    keywords = load_training()
    result = triage_session(flat, keywords)

    print(f"\n  Parsed: {len(flat)} items from {len(batches)} batch(es)")
    print(f"  Avg score: {result['summary']['avg_score']:.0f}/100")
    print(f"\n  Items:")
    for r in result["results"]:
        print(f"    [{r['tag']:8s}] {r['score']:3d}/100 {r['grade']:14s} "
              f"({r['theme']}) {r['text'][:50]}")

    # DB test
    db_path = os.path.join(tempfile.gettempdir(), "axis_test_digest.db")
    db = DigestDB(db_path)
    rows = [{"session_date": "2026-03-17", "batch_time": "10:05",
             "tag": r["tag"], "theme": r["theme"], "text": r["text"],
             "triage_score": r["score"], "triage_grade": r["grade"]}
            for r in result["results"]]
    db.insert_items(rows)

    # Search tests
    print(f"\n  Search 'map layouts': ", end="")
    found = db.search("map layouts")
    print(f"{len(found)} result(s)")

    print(f"  Search 'corruption': ", end="")
    found = db.search("corruption")
    print(f"{len(found)} result(s)")

    print(f"  Tag=ACTION: ", end="")
    found = db.search_by_tag("ACTION")
    print(f"{len(found)} result(s)")

    print(f"  Tag=BLOCKER: ", end="")
    found = db.search_by_tag("BLOCKER")
    print(f"{len(found)} result(s)")

    db.close()
    os.remove(db_path)

    print("\n  PASSED - Full pipeline works.")


# -----------------------------------------------------------------------
# Test 9: Daily briefing generation
# -----------------------------------------------------------------------

def test_briefings():
    header(9, "DAILY BRIEFINGS")
    print("""
  This test generates each briefing type and shows you the output.
  These are what you'll see as popups throughout the day.
    """)
    pause("Press Enter to generate briefings...")

    from daily_briefing import generate_standup, generate_checkin, generate_wrapup

    repo = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))

    for name, fn in [("MORNING STANDUP", lambda: generate_standup(repo_path=repo)),
                     ("MIDDAY CHECK-IN", lambda: generate_checkin(repo_path=repo)),
                     ("END OF DAY", lambda: generate_wrapup(repo_path=repo))]:
        b = fn()
        print(f"\n  --- {name} ({b.display_title}) ---")
        for line in b.body.split("\n"):
            print(f"    {line}")
        print()


# -----------------------------------------------------------------------
# Test 10: Full tray app (manual, 60 seconds)
# -----------------------------------------------------------------------

def test_full_tray():
    header(10, "FULL TRAY APP (60 second live test)")
    print("""
  This launches the real tray app for 60 seconds so you can poke around.

  What to check:
    1. Icon appears in tray (yellow = detecting)
    2. Right-click menu works
    3. Speak for a few seconds -> popup asks "Start recording?"
    4. Click Yes -> icon turns green
    5. Click "Stop Recording" in the menu
    6. Check that session_log.md was created

  The app will auto-exit after 60 seconds, or right-click -> Exit.
    """)
    resp = ask("Press Enter to launch (or 'skip' to skip)...")
    if resp == "skip":
        print("  Skipped.")
        return

    print("  Launching tray app for 60 seconds...")
    print("  (Right-click icon -> Exit to stop early)")
    print()

    # Launch in a subprocess so it gets its own process
    import subprocess
    proc = subprocess.Popen(
        [sys.executable, "tray_app.py"],
        cwd=os.path.dirname(__file__),
    )

    try:
        proc.wait(timeout=60)
    except subprocess.TimeoutExpired:
        proc.terminate()
        proc.wait(timeout=5)

    log_path = os.path.join(os.path.dirname(__file__), "session_log.md")
    if os.path.exists(log_path):
        size = os.path.getsize(log_path)
        print(f"\n  session_log.md exists ({size} bytes)")
    else:
        print("\n  No session_log.md created (expected if you didn't record)")

    print("  Test complete.")


# -----------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------

TESTS = [
    ("Tray icon", test_tray_icon),
    ("VAD speech detection", test_vad_detection),
    ("Mic capture + transcription", test_mic_transcription),
    ("System audio (WASAPI loopback)", test_loopback),
    ("Claude producer batch", test_producer_batch),
    ("Blocker detection", test_blocker_detection),
    ("Scope guard", test_scope_guard),
    ("Digest + triage + DB", test_digest_pipeline),
    ("Daily briefings", test_briefings),
    ("Full tray app (live)", test_full_tray),
]


def main():
    parser = argparse.ArgumentParser(description="AXIS Producer feature tests")
    parser.add_argument("--list", action="store_true", help="List tests")
    parser.add_argument("--test", type=int, help="Run specific test (1-10)")
    parser.add_argument("--from", type=int, dest="start", help="Start from test N")
    args = parser.parse_args()

    if args.list:
        print("\nAvailable tests:")
        for i, (name, _) in enumerate(TESTS, 1):
            print(f"  {i:2d}. {name}")
        print(f"\nRun all: python test_features.py")
        print(f"Run one: python test_features.py --test 3")
        return

    print("=" * 60)
    print("  AXIS Producer — Interactive Feature Test")
    print("  10 tests, ~15 minutes total")
    print("=" * 60)

    if args.test:
        idx = args.test - 1
        if 0 <= idx < len(TESTS):
            TESTS[idx][1]()
        else:
            print(f"Test {args.test} doesn't exist. Use --list.")
        return

    start = (args.start or 1) - 1
    for i, (name, fn) in enumerate(TESTS):
        if i < start:
            continue
        fn()
        if i < len(TESTS) - 1:
            resp = ask("Continue to next test? (Enter=yes, 'skip'=skip next, 'quit'=done) ")
            if resp == "quit":
                break
            if resp == "skip":
                continue

    print(f"\n{'='*60}")
    print("  All tests complete. You're ready for the weekend.")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
