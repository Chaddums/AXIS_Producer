#!/usr/bin/env python3
"""AXIS Producer — Ambient Session Listener.

Runs silently in the background. Listens for voices, transcribes locally
with Whisper, and periodically sends structured notes to a markdown log
via Claude.

Usage:
    python axis_producer.py
    python axis_producer.py --log ./logs/day2.md
    python axis_producer.py --model small.en
    python axis_producer.py --interval 600
    python axis_producer.py --verbose
"""

import argparse
import os
import queue
import signal
import sys
import threading

from capture import AudioCapture
from transcriber import Transcriber
from producer import BatchProducer


def parse_args():
    p = argparse.ArgumentParser(
        description="AXIS Producer — ambient session listener")
    p.add_argument("--log", default="./session_log.md",
                   help="Path to session log file (default: ./session_log.md)")
    p.add_argument("--model", default="base.en",
                   help="Whisper model name (default: base.en)")
    p.add_argument("--interval", type=int, default=300,
                   help="Batch interval in seconds (default: 300)")
    p.add_argument("--device", type=int, default=None,
                   help="Audio input device index (default: system default)")
    p.add_argument("--verbose", action="store_true",
                   help="Print transcript and status to console")
    p.add_argument("--list-devices", action="store_true",
                   help="List available audio input devices and exit")
    return p.parse_args()


def list_audio_devices():
    import sounddevice as sd
    print("\nAvailable audio input devices:\n")
    devices = sd.query_devices()
    for i, d in enumerate(devices):
        if d["max_input_channels"] > 0:
            marker = " <-- default" if i == sd.default.device[0] else ""
            print(f"  [{i}] {d['name']}{marker}")
    print()


def main():
    args = parse_args()

    if args.list_devices:
        list_audio_devices()
        return

    # Verify API key
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY environment variable not set.")
        print("Set it with: set ANTHROPIC_API_KEY=sk-ant-...")
        sys.exit(1)

    # Ensure log directory exists
    log_dir = os.path.dirname(os.path.abspath(args.log))
    os.makedirs(log_dir, exist_ok=True)

    # Shared state
    stop_event = threading.Event()
    chunk_queue = queue.Queue()
    buffer_lock = threading.Lock()
    transcript_buffer: list[str] = []

    # Components
    capture = AudioCapture(chunk_queue, stop_event,
                           device=args.device, verbose=args.verbose)
    transcriber = Transcriber(chunk_queue, stop_event,
                              buffer_lock, transcript_buffer,
                              model_name=args.model, verbose=args.verbose)
    producer = BatchProducer(stop_event, buffer_lock, transcript_buffer,
                             log_path=args.log, interval=args.interval,
                             verbose=args.verbose)

    # Print banner
    import sounddevice as sd
    dev_idx = args.device if args.device is not None else sd.default.device[0]
    dev_name = sd.query_devices(dev_idx)["name"]

    print()
    print("AXIS Producer -- listening")
    print(f"Whisper: {args.model} | VAD: level 2 | Batch: every {args.interval // 60} min")
    print(f"Log: {os.path.abspath(args.log)}")
    print(f"Mic: {dev_name} (device {dev_idx})")
    print("[Ctrl+C to stop] [Ctrl+B to force batch now]")
    print()

    # Start threads
    threads = [
        threading.Thread(target=capture.run, name="capture", daemon=True),
        threading.Thread(target=transcriber.run, name="transcriber", daemon=True),
        threading.Thread(target=producer.run, name="producer", daemon=True),
    ]
    for t in threads:
        t.start()

    # Handle Ctrl+C
    def shutdown(sig, frame):
        print("\n  Shutting down -- flushing final batch...")
        stop_event.set()

    signal.signal(signal.SIGINT, shutdown)

    # Main loop — listen for keyboard input
    try:
        while not stop_event.is_set():
            # On Windows, we can't easily catch Ctrl+B via signal,
            # so we use a simple input loop
            if sys.platform == "win32":
                # Use a short timeout so we can check stop_event
                import msvcrt
                if msvcrt.kbhit():
                    key = msvcrt.getch()
                    if key == b'\x02':  # Ctrl+B
                        print("  [manual] forcing batch...")
                        producer.force_batch.set()
                else:
                    stop_event.wait(timeout=0.1)
            else:
                stop_event.wait(timeout=0.5)
    except KeyboardInterrupt:
        print("\n  Shutting down -- flushing final batch...")
        stop_event.set()

    # Wait for threads to finish (with timeout)
    for t in threads:
        t.join(timeout=5.0)

    print("  AXIS Producer stopped. Log saved to:", os.path.abspath(args.log))

    # Run session digest post-processor
    try:
        from digest import run_digest, DEFAULT_OUTPUT
        print("  Running session digest...")
        run_digest(log_path=args.log, output_path=DEFAULT_OUTPUT, verbose=args.verbose)
    except Exception as e:
        print(f"  [digest] failed (non-fatal): {e}")


if __name__ == "__main__":
    main()
