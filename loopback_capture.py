"""WASAPI Loopback Capture — captures system audio output (Zoom/Teams/Slack callers).

Opens the default output device as a loopback input via sounddevice WasapiSettings.
Downmixes to mono, resamples to 16kHz, runs VAD chunking, and pushes to the shared queue.
"""

import collections
import queue
import struct
import threading

import numpy as np
import sounddevice as sd
import webrtcvad

from capture import (
    SAMPLE_RATE, FRAME_DURATION_MS, FRAME_SIZE, VAD_AGGRESSIVENESS,
    _SILENCE_FRAMES, _MIN_CHUNK_FRAMES,
)


def find_wasapi_loopback_device(device_override: int | None = None) -> int | None:
    """Find the WASAPI output device index suitable for loopback capture.

    If device_override is set, use that directly. Otherwise, find the WASAPI
    device matching the default output device name.
    """
    if device_override is not None:
        return device_override

    try:
        hostapis = sd.query_hostapis()
        wasapi_idx = None
        for i, api in enumerate(hostapis):
            if "wasapi" in api["name"].lower():
                wasapi_idx = i
                break
        if wasapi_idx is None:
            return None

        # Get the default output device name
        default_out = sd.default.device[1]
        if default_out is None or default_out < 0:
            return None

        default_name = sd.query_devices(default_out)["name"].lower()

        # Find the WASAPI device with matching name and output channels
        devices = sd.query_devices()
        for i, d in enumerate(devices):
            if (d["hostapi"] == wasapi_idx
                    and d["max_output_channels"] > 0
                    and default_name[:20] in d["name"].lower()):
                return i

        # Fallback: use the WASAPI host API's default output
        api = hostapis[wasapi_idx]
        default_dev = api.get("default_output_device", -1)
        if default_dev >= 0:
            return default_dev

    except Exception as e:
        print(f"  [loopback] device detection error: {e}")

    return None


class LoopbackCapture:
    """Captures system audio via WASAPI loopback, resamples to 16kHz mono,
    and pushes VAD-chunked audio to the shared queue."""

    def __init__(self, chunk_queue: queue.Queue, stop_event: threading.Event,
                 device: int | None = None, verbose: bool = False):
        self.chunk_queue = chunk_queue
        self.stop_event = stop_event
        self.device = device
        self.verbose = verbose

        self.vad = webrtcvad.Vad(VAD_AGGRESSIVENESS)
        self._silent_count = 0
        self._recording = False
        self._chunk_frames: list[bytes] = []
        self._resample_ratio: float | None = None

    def _resample_to_16k(self, audio: np.ndarray, source_rate: float) -> np.ndarray:
        """Resample audio to 16kHz using linear interpolation."""
        if abs(source_rate - SAMPLE_RATE) < 1:
            return audio

        ratio = SAMPLE_RATE / source_rate
        new_len = int(len(audio) * ratio)
        if new_len == 0:
            return np.array([], dtype=np.float32)

        indices = np.linspace(0, len(audio) - 1, new_len)
        return np.interp(indices, np.arange(len(audio)), audio).astype(np.float32)

    def _audio_callback(self, indata: np.ndarray, frames: int,
                        time_info, status):
        if status and self.verbose:
            print(f"  [loopback] {status}")

        # Downmix to mono
        if indata.shape[1] > 1:
            mono = indata.mean(axis=1)
        else:
            mono = indata[:, 0]

        # Resample to 16kHz
        if self._resample_ratio is not None:
            mono = self._resample_to_16k(mono, self._resample_ratio)

        # Convert to int16 PCM for VAD
        pcm = (mono * 32767).astype(np.int16)

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
                    print("  [loopback] speech detected in system audio")
            self._chunk_frames.append(frame_bytes)
        elif self._recording:
            self._silent_count += 1
            self._chunk_frames.append(frame_bytes)

            if self._silent_count >= _SILENCE_FRAMES:
                self._recording = False
                if len(self._chunk_frames) >= _MIN_CHUNK_FRAMES:
                    audio = self._frames_to_array(self._chunk_frames)
                    self.chunk_queue.put(audio)
                    if self.verbose:
                        dur = len(audio) / SAMPLE_RATE
                        print(f"  [loopback] chunk sealed: {dur:.1f}s")
                self._chunk_frames = []
                self._silent_count = 0

    @staticmethod
    def _frames_to_array(frames: list[bytes]) -> np.ndarray:
        raw = b"".join(frames)
        return np.frombuffer(raw, dtype=np.int16)

    def run(self):
        """Blocking — runs until stop_event is set."""
        dev_idx = find_wasapi_loopback_device(self.device)
        if dev_idx is None:
            print("  [loopback] no WASAPI output device found — loopback disabled")
            return

        dev_info = sd.query_devices(dev_idx)
        source_rate = dev_info["default_samplerate"]
        channels = dev_info["max_output_channels"]

        if abs(source_rate - SAMPLE_RATE) > 1:
            self._resample_ratio = source_rate
        else:
            self._resample_ratio = None

        if self.verbose:
            print(f"  [loopback] device: {dev_info['name']} "
                  f"({source_rate:.0f}Hz, {channels}ch)")

        blocksize = int(source_rate * FRAME_DURATION_MS * 4 / 1000)

        try:
            wasapi = sd.WasapiSettings(loopback=True)
            with sd.InputStream(
                samplerate=source_rate,
                channels=channels,
                dtype="float32",
                blocksize=blocksize,
                device=dev_idx,
                extra_settings=wasapi,
                callback=self._audio_callback,
            ):
                if self.verbose:
                    print("  [loopback] stream open")
                while not self.stop_event.is_set():
                    self.stop_event.wait(timeout=0.1)
        except Exception as e:
            print(f"  [loopback] ERROR: {e}")

        # Flush remaining chunk
        if self._chunk_frames and len(self._chunk_frames) >= _MIN_CHUNK_FRAMES:
            audio = self._frames_to_array(self._chunk_frames)
            self.chunk_queue.put(audio)
