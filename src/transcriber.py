"""Transcription Worker — pulls audio chunks from queue, transcribes with Faster-Whisper."""

import queue
import threading
import time
from datetime import datetime

import numpy as np

WHISPER_MODEL = "base.en"
DEVICE = "cpu"
COMPUTE_TYPE = "int8"
SAMPLE_RATE = 16000


class Transcriber:
    """Background worker that transcribes audio chunks and appends
    timestamped text to a shared transcript buffer."""

    def __init__(self, chunk_queue: queue.Queue, stop_event: threading.Event,
                 buffer_lock: threading.Lock, transcript_buffer: list[str],
                 model_name: str = WHISPER_MODEL, verbose: bool = False):
        self.chunk_queue = chunk_queue
        self.stop_event = stop_event
        self.buffer_lock = buffer_lock
        self.transcript_buffer = transcript_buffer
        self.model_name = model_name
        self.verbose = verbose
        self.ready = threading.Event()  # set once whisper model is loaded
        self._model = None

    def _load_model(self):
        from faster_whisper import WhisperModel
        if self.verbose:
            print(f"  [transcriber] loading Whisper model: {self.model_name}")
        self._model = WhisperModel(self.model_name, device=DEVICE,
                                   compute_type=COMPUTE_TYPE)
        if self.verbose:
            print("  [transcriber] model loaded")

    def run(self):
        """Blocking — runs until stop_event is set and queue is drained."""
        self._load_model()
        self.ready.set()

        while not self.stop_event.is_set():
            try:
                audio_int16 = self.chunk_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            text = self._transcribe(audio_int16)
            if text and text.strip():
                timestamp = datetime.now().strftime("%H:%M")
                line = f"[{timestamp}] {text.strip()}"
                with self.buffer_lock:
                    self.transcript_buffer.append(line)
                if self.verbose:
                    print(f"  [transcriber] {line}")

            self.chunk_queue.task_done()

        # Drain remaining items
        while not self.chunk_queue.empty():
            try:
                audio_int16 = self.chunk_queue.get_nowait()
                text = self._transcribe(audio_int16)
                if text and text.strip():
                    timestamp = datetime.now().strftime("%H:%M")
                    line = f"[{timestamp}] {text.strip()}"
                    with self.buffer_lock:
                        self.transcript_buffer.append(line)
                self.chunk_queue.task_done()
            except queue.Empty:
                break

    def _transcribe(self, audio_int16: np.ndarray) -> str:
        # Faster-whisper expects float32 normalized to [-1, 1]
        audio_f32 = audio_int16.astype(np.float32) / 32768.0

        segments, _ = self._model.transcribe(audio_f32, beam_size=1,
                                              language="en",
                                              vad_filter=True)
        parts = []
        for seg in segments:
            parts.append(seg.text)
        return " ".join(parts)
