"""VAD + Audio Capture — listens to mic, detects speech, queues audio chunks."""

import collections
import queue
import struct
import threading
import time

import numpy as np
import sounddevice as sd
import webrtcvad

SAMPLE_RATE = 16000
FRAME_DURATION_MS = 30
FRAME_SIZE = int(SAMPLE_RATE * FRAME_DURATION_MS / 1000)  # 480 samples
SILENCE_THRESHOLD_SEC = 2.0
MIN_CHUNK_DURATION_SEC = 1.0
VAD_AGGRESSIVENESS = 2

# Number of consecutive silent frames before we seal a chunk
_SILENCE_FRAMES = int(SILENCE_THRESHOLD_SEC * 1000 / FRAME_DURATION_MS)
_MIN_CHUNK_FRAMES = int(MIN_CHUNK_DURATION_SEC * 1000 / FRAME_DURATION_MS)


class AudioCapture:
    """Continuously captures mic audio, uses VAD to detect speech chunks,
    and pushes completed chunks (as int16 numpy arrays) onto an output queue."""

    def __init__(self, chunk_queue: queue.Queue, stop_event: threading.Event,
                 device: int | None = None, verbose: bool = False):
        self.chunk_queue = chunk_queue
        self.stop_event = stop_event
        self.device = device
        self.verbose = verbose

        self.vad = webrtcvad.Vad(VAD_AGGRESSIVENESS)

        self._ring = collections.deque()
        self._silent_count = 0
        self._recording = False
        self._chunk_frames: list[bytes] = []

    def _audio_callback(self, indata: np.ndarray, frames: int,
                        time_info, status):
        if status and self.verbose:
            print(f"  [capture] {status}")

        # Convert float32 → int16 PCM (what webrtcvad expects)
        pcm = (indata[:, 0] * 32767).astype(np.int16)

        # Split into VAD-sized frames
        offset = 0
        while offset + FRAME_SIZE <= len(pcm):
            frame_bytes = struct.pack(f"{FRAME_SIZE}h",
                                      *pcm[offset:offset + FRAME_SIZE])
            self._process_frame(frame_bytes)
            offset += FRAME_SIZE

    def _process_frame(self, frame_bytes: bytes):
        is_speech = self.vad.is_speech(frame_bytes, SAMPLE_RATE)

        if is_speech:
            self._silent_count = 0
            if not self._recording:
                self._recording = True
                if self.verbose:
                    print("  [capture] speech detected")
            self._chunk_frames.append(frame_bytes)
        elif self._recording:
            self._silent_count += 1
            self._chunk_frames.append(frame_bytes)

            if self._silent_count >= _SILENCE_FRAMES:
                # Seal the chunk
                self._recording = False
                if len(self._chunk_frames) >= _MIN_CHUNK_FRAMES:
                    audio = self._frames_to_array(self._chunk_frames)
                    self.chunk_queue.put(audio)
                    if self.verbose:
                        dur = len(audio) / SAMPLE_RATE
                        print(f"  [capture] chunk sealed: {dur:.1f}s")
                elif self.verbose:
                    print("  [capture] chunk too short, discarded")
                self._chunk_frames = []
                self._silent_count = 0

    @staticmethod
    def _frames_to_array(frames: list[bytes]) -> np.ndarray:
        raw = b"".join(frames)
        return np.frombuffer(raw, dtype=np.int16)

    def run(self):
        """Blocking — runs until stop_event is set."""
        # Large blocksize so we get smooth frames
        blocksize = FRAME_SIZE * 4

        try:
            with sd.InputStream(samplerate=SAMPLE_RATE, channels=1,
                                dtype="float32", blocksize=blocksize,
                                device=self.device,
                                callback=self._audio_callback):
                if self.verbose:
                    print("  [capture] mic stream open")
                while not self.stop_event.is_set():
                    self.stop_event.wait(timeout=0.1)
        except Exception as e:
            print(f"  [capture] ERROR: {e}")
            self.stop_event.set()

        # Flush any remaining chunk
        if self._chunk_frames and len(self._chunk_frames) >= _MIN_CHUNK_FRAMES:
            audio = self._frames_to_array(self._chunk_frames)
            self.chunk_queue.put(audio)
