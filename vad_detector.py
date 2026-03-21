"""Lightweight VAD detector — always-on speech presence detection.

Opens the mic at 16kHz mono and runs webrtcvad on each frame.
When speech is detected (10 consecutive voiced frames = 300ms),
fires the on_speech_detected callback. 30-second cooldown before re-triggering.
"""

import struct
import threading
import time

import numpy as np
import sounddevice as sd
import webrtcvad

from capture import SAMPLE_RATE, FRAME_DURATION_MS, FRAME_SIZE, CALIBRATION_SEC, NOISE_GATE_HEADROOM

VOICED_FRAMES_THRESHOLD = 10   # 300ms of speech to trigger
COOLDOWN_SEC = 30.0            # no re-trigger for 30s


class VadDetector:
    """Lightweight speech presence detector. Does NOT accumulate audio."""

    def __init__(self, on_speech_detected, device: int | None = None,
                 sensitivity: int = 1, verbose: bool = False):
        self.on_speech_detected = on_speech_detected
        self.device = device
        self.verbose = verbose

        self.vad = webrtcvad.Vad(sensitivity)
        self._noise_gate_rms = 0
        self._voiced_count = 0
        self._last_trigger = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _audio_callback(self, indata: np.ndarray, frames: int,
                        time_info, status):
        if self._stop.is_set():
            return

        pcm = (indata[:, 0] * 32767).astype(np.int16)

        # Resample to 16000Hz if device runs at different rate
        if getattr(self, '_needs_resample', False):
            ratio = SAMPLE_RATE / self._native_rate
            new_len = int(len(pcm) * ratio)
            indices = np.arange(new_len) / ratio
            pcm = pcm[np.clip(indices.astype(int), 0, len(pcm) - 1)]

        offset = 0
        while offset + FRAME_SIZE <= len(pcm):
            frame_bytes = struct.pack(f"{FRAME_SIZE}h",
                                      *pcm[offset:offset + FRAME_SIZE])
            self._process_frame(frame_bytes)
            offset += FRAME_SIZE

    def _process_frame(self, frame_bytes: bytes):
        # Noise gate: reject low-energy frames before trusting VAD
        pcm_frame = np.frombuffer(frame_bytes, dtype=np.int16)
        rms = np.sqrt(np.mean(pcm_frame.astype(np.float64) ** 2))
        if rms < self._noise_gate_rms:
            is_speech = False
        else:
            is_speech = self.vad.is_speech(frame_bytes, SAMPLE_RATE)

        if is_speech:
            self._voiced_count += 1
            if self._voiced_count >= VOICED_FRAMES_THRESHOLD:
                now = time.time()
                if now - self._last_trigger > COOLDOWN_SEC:
                    self._last_trigger = now
                    self._voiced_count = 0
                    if self.verbose:
                        print("  [vad] speech detected -- triggering callback")
                    self.on_speech_detected()
        else:
            self._voiced_count = 0

    def _calibrate_noise_floor(self, native_rate, blocksize):
        """Record ambient noise to set the noise gate threshold."""
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

        with sd.InputStream(samplerate=native_rate, channels=1,
                            dtype="float32", blocksize=blocksize,
                            device=self.device, callback=cal_callback):
            time.sleep(CALIBRATION_SEC)

        if rms_samples:
            p90 = float(np.percentile(rms_samples, 90))
            self._noise_gate_rms = p90 * NOISE_GATE_HEADROOM
        else:
            self._noise_gate_rms = 600

    def _run(self):
        # Query device native sample rate
        try:
            dev_info = sd.query_devices(self.device)
            native_rate = int(dev_info['default_samplerate'])
        except Exception:
            native_rate = SAMPLE_RATE

        self._native_rate = native_rate
        self._needs_resample = (native_rate != SAMPLE_RATE)

        blocksize = int(native_rate * FRAME_DURATION_MS / 1000) * 4

        # Calibrate noise gate
        self._calibrate_noise_floor(native_rate, blocksize)

        try:
            with sd.InputStream(samplerate=native_rate, channels=1,
                                dtype="float32", blocksize=blocksize,
                                device=self.device,
                                callback=self._audio_callback):
                if self.verbose:
                    print(f"  [vad] detector started ({native_rate}Hz, gate={self._noise_gate_rms:.0f})")
                while not self._stop.is_set():
                    self._stop.wait(timeout=0.2)
        except Exception as e:
            print(f"  [vad] ERROR: {e}")

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._voiced_count = 0
        self._thread = threading.Thread(target=self._run, name="vad-detector",
                                        daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3.0)
            self._thread = None
