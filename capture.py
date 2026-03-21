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
VAD_AGGRESSIVENESS = 3

# Adaptive noise gate calibration
CALIBRATION_SEC = 3.0          # seconds of ambient noise to measure at startup
NOISE_GATE_HEADROOM = 1.5     # gate = ambient_p90 * this multiplier

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
        self._noise_gate_rms = 0  # set during calibration

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

        # Resample from native rate to 16000Hz if needed
        if getattr(self, '_needs_resample', False):
            ratio = SAMPLE_RATE / self._native_rate
            new_len = int(len(pcm) * ratio)
            indices = np.arange(new_len) / ratio
            pcm = pcm[np.clip(indices.astype(int), 0, len(pcm) - 1)]

        # Split into VAD-sized frames
        offset = 0
        while offset + FRAME_SIZE <= len(pcm):
            frame_bytes = struct.pack(f"{FRAME_SIZE}h",
                                      *pcm[offset:offset + FRAME_SIZE])
            self._process_frame(frame_bytes)
            offset += FRAME_SIZE

    def _process_frame(self, frame_bytes: bytes):
        # Noise gate: check RMS energy before trusting VAD
        pcm_frame = np.frombuffer(frame_bytes, dtype=np.int16)
        rms = np.sqrt(np.mean(pcm_frame.astype(np.float64) ** 2))
        if rms < self._noise_gate_rms:
            is_speech = False
        else:
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

    def _calibrate_noise_floor(self, native_rate: int, blocksize: int):
        """Record a few seconds of ambient noise to set the noise gate."""
        rms_samples = []

        def cal_callback(indata, frames, time_info, status):
            pcm = (indata[:, 0] * 32767).astype(np.int16)
            if getattr(self, '_needs_resample', False):
                ratio = SAMPLE_RATE / self._native_rate
                new_len = int(len(pcm) * ratio)
                indices = np.arange(new_len) / ratio
                pcm = pcm[np.clip(indices.astype(int), 0, len(pcm) - 1)]
            offset = 0
            while offset + FRAME_SIZE <= len(pcm):
                pf = pcm[offset:offset + FRAME_SIZE]
                rms = np.sqrt(np.mean(pf.astype(np.float64) ** 2))
                rms_samples.append(rms)
                offset += FRAME_SIZE

        if self.verbose:
            print(f"  [capture] calibrating noise floor ({CALIBRATION_SEC:.0f}s)...")

        with sd.InputStream(samplerate=native_rate, channels=1,
                            dtype="float32", blocksize=blocksize,
                            device=self.device, callback=cal_callback):
            time.sleep(CALIBRATION_SEC)

        if rms_samples:
            p90 = float(np.percentile(rms_samples, 90))
            self._noise_gate_rms = p90 * NOISE_GATE_HEADROOM
            if self.verbose:
                avg = float(np.mean(rms_samples))
                print(f"  [capture] noise floor: avg={avg:.0f} p90={p90:.0f} -> gate={self._noise_gate_rms:.0f}")
        else:
            self._noise_gate_rms = 600  # safe fallback
            if self.verbose:
                print(f"  [capture] calibration got no samples, using fallback gate={self._noise_gate_rms}")

    def run(self):
        """Blocking — runs until stop_event is set."""
        # Query device native sample rate — WASAPI devices won't resample
        try:
            dev_info = sd.query_devices(self.device)
            native_rate = int(dev_info['default_samplerate'])
        except Exception:
            native_rate = SAMPLE_RATE

        self._native_rate = native_rate
        self._needs_resample = (native_rate != SAMPLE_RATE)
        if self._needs_resample and self.verbose:
            print(f"  [capture] device runs at {native_rate}Hz, will resample to {SAMPLE_RATE}Hz")

        blocksize = int(native_rate * FRAME_DURATION_MS / 1000) * 4

        # Calibrate noise gate before opening the main stream
        self._calibrate_noise_floor(native_rate, blocksize)

        try:
            with sd.InputStream(samplerate=native_rate, channels=1,
                                dtype="float32", blocksize=blocksize,
                                device=self.device,
                                callback=self._audio_callback):
                if self.verbose:
                    print("  [capture] mic stream open -- listening")
                while not self.stop_event.is_set():
                    self.stop_event.wait(timeout=0.1)
        except Exception as e:
            print(f"  [capture] ERROR: {e}")
            self.stop_event.set()

        # Flush any remaining chunk
        if self._chunk_frames and len(self._chunk_frames) >= _MIN_CHUNK_FRAMES:
            audio = self._frames_to_array(self._chunk_frames)
            self.chunk_queue.put(audio)
