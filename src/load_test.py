#!/usr/bin/env python3
"""AXIS Load Test — bot army that consumes YouTube content and pushes events.

Creates N bot workers, each consuming a YouTube video (audio + transcript).
Each bot runs the full AXIS pipeline: transcription → LLM analysis → events.
Simulates a busy team with concurrent sessions.

Usage:
    # 5 bots, each watching a different video
    python load_test.py --bots 5

    # 20 bots, Groq for transcription, staggered start
    python load_test.py --bots 20 --transcribe groq --stagger 10

    # 100 bots, captions only (no Whisper), max throughput
    python load_test.py --bots 100 --transcribe captions --stagger 2

    # Custom videos
    python load_test.py --bots 3 --urls "URL1,URL2,URL3"

Modes:
    --transcribe captions   Use YouTube auto-captions (free, instant, unlimited)
    --transcribe groq       Use Groq Whisper API (fast, ~$0.11/hr audio)
    --transcribe whisper    Use local Whisper (CPU-bound, ~3 bots max)
"""

import argparse
import asyncio
import json
import os
import random
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

from backend_client import BackendClient
from llm_provider import call_llm, DEFAULT_MODELS
from producer import SYSTEM_PROMPT, MAX_TOKENS

# Bot personas — names, roles, conversation styles
BOT_PERSONAS = [
    "Alex Chen", "Maria Santos", "Jordan Kim", "Sam Patel", "Taylor Morgan",
    "Riley Johnson", "Casey O'Brien", "Quinn Williams", "Avery Thompson", "Drew Garcia",
    "Harper Lee", "Morgan Davis", "Jamie Wilson", "Parker Brown", "Skyler Martinez",
    "Reese Anderson", "Dakota Thomas", "Finley Jackson", "Rowan White", "Sage Harris",
    "Blake Robinson", "Charlie Lewis", "Emerson Clark", "Hayden Walker", "Kai Young",
    "Lane Hall", "Marley Allen", "Noel King", "Oakley Wright", "Peyton Scott",
    "Remy Green", "Shiloh Baker", "Tatum Adams", "Val Nelson", "Winter Hill",
    "Zion Campbell", "Ashton Mitchell", "Blair Roberts", "Cameron Turner", "Dallas Phillips",
    "Eden Evans", "Frankie Edwards", "Gray Collins", "Haven Stewart", "Indigo Sanchez",
    "Jules Morris", "Kendall Rogers", "Lake Reed", "Milan Cook", "North Morgan",
    "Onyx Bailey", "Phoenix Rivera", "Rain Cooper", "Storm Richardson", "True Cox",
    "Unity Howard", "Vesper Ward", "Wren Torres", "Xen Peterson", "Yael Gray",
    "Zen Ramirez", "Arrow James", "Briar Watson", "Cedar Brooks", "Dune Kelly",
    "Elm Sanders", "Fern Price", "Glen Bennett", "Haze Wood", "Iris Barnes",
    "Jet Ross", "Kit Henderson", "Lark Coleman", "Moss Jenkins", "Neve Perry",
    "Opal Powell", "Pine Long", "Quill Patterson", "Reed Hughes", "Sol Flores",
    "Thorn Washington", "Uma Butler", "Vale Simmons", "Wynn Foster", "Xander Gonzales",
    "Yew Bryant", "Zara Alexander", "Alder Russell", "Birch Griffin", "Cliff Diaz",
    "Dell Hayes", "Echo Myers", "Ford Ford", "Garnet Hamilton", "Heath Graham",
    "Ivy Sullivan", "Jay Wallace", "Koa West", "Leaf Cole", "Mist Jordan",
    "Nyx Owens", "Oak Reynolds", "Palm Fisher", "Quest Ellis", "Rue Harrison",
]

# Default YouTube videos for testing (long-form conversations/podcasts)
DEFAULT_VIDEOS = [
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",  # placeholder — user should supply real ones
]


def get_captions(video_url: str, temp_dir: str) -> list[dict]:
    """Pull YouTube auto-captions via yt-dlp. Returns list of {start, duration, text} segments."""
    out_path = os.path.join(temp_dir, "subs")
    try:
        subprocess.run([
            sys.executable, "-m", "yt_dlp",
            "--skip-download",
            "--write-auto-sub",
            "--sub-lang", "en",
            "--sub-format", "json3",
            "-o", out_path,
            video_url,
        ], capture_output=True, timeout=60, check=True)
    except subprocess.CalledProcessError as e:
        print(f"  [yt-dlp] Caption download failed: {e.stderr[:200] if e.stderr else 'unknown'}")
        return []

    # Find the subtitle file
    for f in Path(temp_dir).glob("subs*.json3"):
        with open(f, encoding="utf-8") as fh:
            data = json.load(fh)
        # json3 format has events with segs
        segments = []
        for event in data.get("events", []):
            text_parts = []
            for seg in event.get("segs", []):
                t = seg.get("utf8", "").strip()
                if t and t != "\n":
                    text_parts.append(t)
            if text_parts:
                segments.append({
                    "start": event.get("tStartMs", 0) / 1000.0,
                    "text": " ".join(text_parts),
                })
        return segments
    return []


def get_audio_chunks(video_url: str, temp_dir: str, chunk_seconds: int = 300) -> list[str]:
    """Download YouTube audio and split into chunks. Returns list of file paths."""
    audio_path = os.path.join(temp_dir, "audio.wav")
    try:
        subprocess.run([
            sys.executable, "-m", "yt_dlp",
            "-x", "--audio-format", "wav",
            "--audio-quality", "0",
            "-o", audio_path,
            video_url,
        ], capture_output=True, timeout=300, check=True)
    except subprocess.CalledProcessError as e:
        print(f"  [yt-dlp] Audio download failed: {e.stderr[:200] if e.stderr else 'unknown'}")
        return []

    # Find the actual output file (yt-dlp may add extension)
    actual = None
    for f in Path(temp_dir).glob("audio*"):
        actual = str(f)
        break
    if not actual or not os.path.exists(actual):
        return []

    # Split into chunks using ffmpeg (via yt-dlp's bundled ffmpeg)
    chunks = []
    i = 0
    while True:
        chunk_path = os.path.join(temp_dir, f"chunk_{i:04d}.wav")
        result = subprocess.run([
            "ffmpeg", "-y", "-i", actual,
            "-ss", str(i * chunk_seconds),
            "-t", str(chunk_seconds),
            "-ac", "1", "-ar", "16000",
            chunk_path,
        ], capture_output=True, timeout=120)
        if result.returncode != 0 or not os.path.exists(chunk_path) or os.path.getsize(chunk_path) < 1000:
            break
        chunks.append(chunk_path)
        i += 1
    return chunks


def chunks_from_captions(segments: list[dict], chunk_seconds: int = 300) -> list[str]:
    """Group caption segments into time-based chunks of transcript text."""
    if not segments:
        return []
    chunks = []
    current = []
    chunk_start = 0

    for seg in segments:
        if seg["start"] >= chunk_start + chunk_seconds and current:
            chunks.append(" ".join(current))
            current = []
            chunk_start = seg["start"]
        current.append(seg["text"])

    if current:
        chunks.append(" ".join(current))
    return chunks


def transcribe_with_whisper(audio_path: str, model_name: str = "base.en") -> str:
    """Local Whisper transcription."""
    from faster_whisper import WhisperModel
    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    segments, _ = model.transcribe(audio_path)
    return " ".join(s.text.strip() for s in segments)


def transcribe_with_groq(audio_path: str, backend: BackendClient, team_id: str) -> str:
    """Groq cloud transcription via backend proxy."""
    with open(audio_path, "rb") as f:
        audio_bytes = f.read()
    result = backend.groq_transcribe(team_id, audio_bytes)
    if result and not result.get("_error"):
        return result.get("text", "")
    return ""


def analyze_batch(transcript: str, provider: str, api_key: str = "",
                  model: str = "", backend: BackendClient = None,
                  team_id: str = "", ollama_url: str = "http://localhost:11434") -> str:
    """Run LLM analysis on a transcript chunk — same pipeline as the real producer."""
    if backend and team_id and provider == "anthropic" and not api_key:
        result = backend.claude_batch(
            team_id, SYSTEM_PROMPT, transcript,
            model=model or DEFAULT_MODELS["anthropic"],
            max_tokens=MAX_TOKENS,
        )
        if result and not result.get("_error"):
            content = result.get("content", [])
            return content[0].get("text", "") if content else ""
        raise RuntimeError(f"Backend proxy error: {result}")
    else:
        return call_llm(
            provider=provider, system=SYSTEM_PROMPT,
            user_message=transcript, api_key=api_key,
            model=model, max_tokens=MAX_TOKENS,
            ollama_url=ollama_url,
        )


def extract_items(notes: str) -> list[tuple[str, str]]:
    """Parse structured notes into (category, text) tuples."""
    items = []
    current_category = None
    for line in notes.split("\n"):
        line = line.strip()
        if line.startswith("## "):
            current_category = line[3:].strip()
        elif line.startswith("- ") and current_category:
            items.append((current_category, line[2:].strip()))
    return items


def push_events(items: list[tuple[str, str]], who: str,
                backend: BackendClient, team_id: str):
    """Push extracted items as events to the backend."""
    CATEGORY_TO_EVENT_TYPE = {
        "Decisions Locked": "decision",
        "Ideas Generated": "idea",
        "Open Questions": "question",
        "Action Items": "action_item",
        "Watch List": "watch",
        "Blockers": "blocker",
        "Key Discussion": "discussion",
    }
    CATEGORY_TO_PRIORITY = {
        "Decisions Locked": "success",
        "Ideas Generated": "info",
        "Open Questions": "info",
        "Action Items": "warning",
        "Watch List": "warning",
        "Blockers": "critical",
        "Key Discussion": "ambient",
    }
    events = []
    for category, text in items:
        event_type = CATEGORY_TO_EVENT_TYPE.get(category, "note")
        priority = CATEGORY_TO_PRIORITY.get(category, "info")
        events.append({
            "team_id": team_id,
            "session_id": f"loadtest_{who.lower().replace(' ', '_')}",
            "stream": "voice",
            "event_type": event_type,
            "who": who,
            "area": category,
            "files": [],
            "summary": text,
            "raw": {"source": "load_test", "category": category, "priority": priority},
        })
    if events:
        count = backend.push_events(events)
        return count
    return 0


class BotWorker:
    """A single bot that consumes content and pushes events."""

    def __init__(self, bot_id: int, name: str, video_url: str,
                 backend: BackendClient, team_id: str,
                 transcribe_mode: str = "captions",
                 llm_provider: str = "anthropic", llm_api_key: str = "",
                 llm_model: str = "", ollama_url: str = "http://localhost:11434",
                 batch_interval: float = 30.0, verbose: bool = False):
        self.bot_id = bot_id
        self.name = name
        self.video_url = video_url
        self.backend = backend
        self.team_id = team_id
        self.transcribe_mode = transcribe_mode
        self.llm_provider = llm_provider
        self.llm_api_key = llm_api_key
        self.llm_model = llm_model
        self.ollama_url = ollama_url
        self.batch_interval = batch_interval
        self.verbose = verbose
        self.stats = {"batches": 0, "events": 0, "errors": 0}

    def run(self):
        """Main bot loop — download content, process batches, push events."""
        tag = f"[bot-{self.bot_id} {self.name}]"
        print(f"  {tag} starting — video: {self.video_url[:60]}...")

        with tempfile.TemporaryDirectory(prefix=f"axis_bot_{self.bot_id}_") as temp_dir:
            # Get transcript chunks
            if self.transcribe_mode == "captions":
                print(f"  {tag} pulling captions...")
                segments = get_captions(self.video_url, temp_dir)
                chunks = chunks_from_captions(segments, chunk_seconds=300)
                if not chunks:
                    print(f"  {tag} no captions found, aborting")
                    return
                print(f"  {tag} got {len(chunks)} caption chunks")
            elif self.transcribe_mode in ("groq", "whisper"):
                print(f"  {tag} downloading audio...")
                audio_chunks = get_audio_chunks(self.video_url, temp_dir)
                if not audio_chunks:
                    print(f"  {tag} no audio, aborting")
                    return
                print(f"  {tag} got {len(audio_chunks)} audio chunks, transcribing...")
                chunks = []
                for ac in audio_chunks:
                    if self.transcribe_mode == "groq":
                        text = transcribe_with_groq(ac, self.backend, self.team_id)
                    else:
                        text = transcribe_with_whisper(ac)
                    if text.strip():
                        chunks.append(text)
                print(f"  {tag} transcribed {len(chunks)} chunks")
            else:
                print(f"  {tag} unknown mode: {self.transcribe_mode}")
                return

            # Send presence event
            self.backend.push_events([{
                "team_id": self.team_id,
                "session_id": f"loadtest_{self.name.lower().replace(' ', '_')}",
                "stream": "presence",
                "event_type": "presence",
                "who": self.name,
                "summary": "online",
                "raw": {"source": "load_test"},
            }])

            # Process each chunk through the LLM and push events
            for i, chunk in enumerate(chunks):
                word_count = len(chunk.split())
                if word_count < 20:
                    continue

                print(f"  {tag} batch {i+1}/{len(chunks)} — {word_count} words")

                try:
                    notes = analyze_batch(
                        chunk, self.llm_provider, self.llm_api_key,
                        self.llm_model, self.backend, self.team_id,
                        self.ollama_url,
                    )
                    items = extract_items(notes)
                    if items:
                        count = push_events(items, self.name, self.backend, self.team_id)
                        self.stats["batches"] += 1
                        self.stats["events"] += count
                        if self.verbose:
                            print(f"  {tag} pushed {count} events")
                    else:
                        print(f"  {tag} batch {i+1} — no items extracted")
                except Exception as e:
                    self.stats["errors"] += 1
                    print(f"  {tag} ERROR batch {i+1}: {e}")

                # Wait between batches to simulate real-time
                if i < len(chunks) - 1:
                    time.sleep(self.batch_interval)

        print(f"  {tag} done — {self.stats['batches']} batches, "
              f"{self.stats['events']} events, {self.stats['errors']} errors")


def main():
    parser = argparse.ArgumentParser(description="AXIS Load Test — bot army")
    parser.add_argument("--bots", type=int, default=5, help="Number of bot workers")
    parser.add_argument("--urls", type=str, default="",
                        help="Comma-separated YouTube URLs (cycles if fewer than bots)")
    parser.add_argument("--transcribe", choices=["captions", "groq", "whisper"],
                        default="captions", help="Transcription mode")
    parser.add_argument("--llm", default="anthropic",
                        help="LLM provider for analysis (anthropic, groq, ollama, etc.)")
    parser.add_argument("--llm-key", default="", help="LLM API key (if BYOK)")
    parser.add_argument("--llm-model", default="", help="LLM model override")
    parser.add_argument("--stagger", type=float, default=5.0,
                        help="Seconds between bot launches")
    parser.add_argument("--batch-interval", type=float, default=90.0,
                        help="Seconds between batches per bot (default 90s, real sessions use 300s)")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    # Load settings for backend connection
    from settings import Settings
    settings = Settings.load()

    if not settings.backend_url or not settings.auth_token or not settings.team_id:
        print("ERROR: backend_url, auth_token, and team_id must be configured.")
        print("Run the NUX setup first.")
        sys.exit(1)

    backend = BackendClient(settings.backend_url, settings.auth_token)
    if not backend.connected:
        print(f"WARNING: Backend at {settings.backend_url} not reachable. Events may fail.")

    # Parse video URLs
    if args.urls:
        videos = [u.strip() for u in args.urls.split(",") if u.strip()]
    else:
        videos = DEFAULT_VIDEOS
        print("No --urls provided. Supply YouTube URLs for meaningful test data:")
        print('  python load_test.py --bots 10 --urls "URL1,URL2,URL3"')
        print()

    # Resolve LLM API key
    llm_key = args.llm_key or settings.llm_api_key
    if args.llm == "anthropic" and not llm_key:
        # Use backend proxy (hosted mode)
        llm_key = ""

    print("=" * 60)
    print(f"AXIS Load Test")
    print(f"  Bots:        {args.bots}")
    print(f"  Videos:      {len(videos)}")
    print(f"  Transcribe:  {args.transcribe}")
    print(f"  LLM:         {args.llm}")
    print(f"  Backend:     {settings.backend_url}")
    print(f"  Team:        {settings.team_id}")
    print(f"  Stagger:     {args.stagger}s")
    print(f"  Batch every: {args.batch_interval}s")
    print("=" * 60)

    # Create bot workers
    threads = []
    for i in range(args.bots):
        name = BOT_PERSONAS[i % len(BOT_PERSONAS)]
        video = videos[i % len(videos)]
        bot = BotWorker(
            bot_id=i,
            name=name,
            video_url=video,
            backend=backend,
            team_id=settings.team_id,
            transcribe_mode=args.transcribe,
            llm_provider=args.llm,
            llm_api_key=llm_key,
            llm_model=args.llm_model,
            ollama_url=settings.ollama_url,
            batch_interval=args.batch_interval,
            verbose=args.verbose,
        )
        t = threading.Thread(target=bot.run, name=f"bot-{i}-{name}", daemon=True)
        threads.append((t, bot))

    # Launch with stagger
    print(f"\nLaunching {args.bots} bots...")
    for i, (t, bot) in enumerate(threads):
        t.start()
        if i < len(threads) - 1:
            time.sleep(args.stagger)

    # Wait for all to finish
    for t, bot in threads:
        t.join()

    # Summary
    print("\n" + "=" * 60)
    print("LOAD TEST COMPLETE")
    total_batches = sum(b.stats["batches"] for _, b in threads)
    total_events = sum(b.stats["events"] for _, b in threads)
    total_errors = sum(b.stats["errors"] for _, b in threads)
    print(f"  Bots:    {args.bots}")
    print(f"  Batches: {total_batches}")
    print(f"  Events:  {total_events}")
    print(f"  Errors:  {total_errors}")
    print("=" * 60)


if __name__ == "__main__":
    main()
