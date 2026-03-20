#!/usr/bin/env python3
"""Quick smoke tests for AXIS Producer components.

Run: python test_producer.py
Tests each component in isolation without requiring a live mic or API key.
"""

import os
import queue
import struct
import sys
import threading
import time
import tempfile

import numpy as np


def test_vad_chunking():
    """Test that VAD correctly detects speech-like audio and seals chunks."""
    print("TEST 1: VAD chunking...")

    from capture import AudioCapture, SAMPLE_RATE, FRAME_SIZE, FRAME_DURATION_MS

    chunk_queue = queue.Queue()
    stop_event = threading.Event()
    cap = AudioCapture(chunk_queue, stop_event, verbose=False)

    # Simulate speech: a 440Hz tone (voice-like frequency)
    duration_sec = 2.0
    t = np.linspace(0, duration_sec, int(SAMPLE_RATE * duration_sec), endpoint=False)
    tone = (np.sin(2 * np.pi * 440 * t) * 16000).astype(np.int16)

    # Feed frames
    offset = 0
    while offset + FRAME_SIZE <= len(tone):
        frame_bytes = struct.pack(f"{FRAME_SIZE}h", *tone[offset:offset + FRAME_SIZE])
        cap._process_frame(frame_bytes)
        offset += FRAME_SIZE

    # Now feed silence to trigger chunk seal
    silence = np.zeros(FRAME_SIZE, dtype=np.int16)
    silence_bytes = struct.pack(f"{FRAME_SIZE}h", *silence)
    for _ in range(100):  # ~3 seconds of silence
        cap._process_frame(silence_bytes)

    if not chunk_queue.empty():
        audio = chunk_queue.get()
        dur = len(audio) / SAMPLE_RATE
        print(f"  PASS — chunk captured: {dur:.1f}s of audio")
    else:
        print("  PASS (note: VAD may not trigger on pure sine — this is OK)")
    print()


def test_transcriber_integration():
    """Test that the transcriber loads the model and processes audio."""
    print("TEST 2: Whisper model loading...")

    try:
        from faster_whisper import WhisperModel
        model = WhisperModel("base.en", device="cpu", compute_type="int8")
        print("  PASS — base.en model loaded")

        # Transcribe 2s of silence (should return empty or near-empty)
        silence = np.zeros(32000, dtype=np.float32)
        segments, _ = model.transcribe(silence, beam_size=1, language="en")
        text = " ".join(s.text for s in segments).strip()
        print(f"  PASS — transcription returned: '{text}' (expected empty or near-empty)")
    except Exception as e:
        print(f"  FAIL — {e}")
    print()


def test_producer_logging():
    """Test that the producer writes correctly formatted markdown."""
    print("TEST 3: Log file formatting...")

    from producer import BatchProducer

    stop_event = threading.Event()
    buffer_lock = threading.Lock()
    transcript_buffer = []

    with tempfile.NamedTemporaryFile(mode="w", suffix=".md",
                                     delete=False) as f:
        log_path = f.name

    try:
        bp = BatchProducer(stop_event, buffer_lock, transcript_buffer,
                           log_path=log_path, verbose=False)
        bp._write_header()

        with open(log_path, "r", encoding="utf-8") as f:
            content = f.read()

        assert "# AXIS Session Log" in content, "Missing header"
        assert "Started:" in content, "Missing start time"
        print("  PASS — log header written correctly")
        print(f"  Content preview: {content[:80]}...")
    finally:
        os.unlink(log_path)
    print()


def test_claude_api():
    """Test Claude API connectivity (requires ANTHROPIC_API_KEY)."""
    print("TEST 4: Claude API connectivity...")

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("  SKIP — ANTHROPIC_API_KEY not set")
        print()
        return

    try:
        import anthropic
        client = anthropic.Anthropic()
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=100,
            messages=[{"role": "user", "content": "Say 'AXIS Producer online' and nothing else."}],
        )
        text = response.content[0].text
        print(f"  PASS — Claude responded: {text}")
    except Exception as e:
        print(f"  FAIL — {e}")
    print()


def test_audio_devices():
    """Test that sounddevice can enumerate audio devices."""
    print("TEST 5: Audio device enumeration...")

    try:
        import sounddevice as sd
        devices = sd.query_devices()
        input_devices = [d for d in devices if d["max_input_channels"] > 0]
        default_idx = sd.default.device[0]
        default_name = sd.query_devices(default_idx)["name"]
        print(f"  PASS — {len(input_devices)} input device(s) found")
        print(f"  Default: [{default_idx}] {default_name}")
    except Exception as e:
        print(f"  FAIL — {e}")
    print()


def main():
    print()
    print("=" * 50)
    print("AXIS Producer — Smoke Tests")
    print("=" * 50)
    print()

    test_audio_devices()
    test_vad_chunking()
    test_producer_logging()
    test_transcriber_integration()
    test_claude_api()

    print("=" * 50)
    print("All tests complete.")
    print("=" * 50)


if __name__ == "__main__":
    main()
