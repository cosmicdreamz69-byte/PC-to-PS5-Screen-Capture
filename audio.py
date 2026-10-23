"""Live audio playback through the PC's default output device.

Remote Play audio frames (decoded to ``av.AudioFrame``) are resampled to packed
float32 and fed to a sounddevice output stream. A callback pulls from a small
ring buffer, so decode-thread jitter doesn't cause glitches, and the buffer is
capped so latency can't grow without bound.
"""
from __future__ import annotations

import collections
import threading

import av
import numpy as np

try:
    import sounddevice as sd
except Exception:  # noqa: BLE001 - audio is optional; app still runs without it
    sd = None

# Drop the oldest audio if more than this many seconds pile up (keeps A/V close).
_MAX_BUFFER_SECONDS = 0.4


class AudioPlayer:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._buf = collections.deque()  # chunks of shape (samples, channels)
        self._buffered = 0               # total buffered samples
        self._stream = None
        self._resampler = None
        self._channels = 2
        self._rate = 48000
        self._muted = False

    @property
    def available(self) -> bool:
        return sd is not None

    def set_muted(self, muted: bool) -> None:
        self._muted = bool(muted)
        if muted:
            with self._lock:
                self._buf.clear()
                self._buffered = 0

    def feed(self, frame) -> None:
        """Called from the stream thread with an av.AudioFrame."""
        if sd is None or self._muted:
            return
        try:
            if self._stream is None:
                self._open(frame)
            out = self._resampler.resample(frame)
            out = out[0] if isinstance(out, list) else out
            if out is None:
                return
            data = out.to_ndarray().reshape(-1, self._channels).astype(np.float32)
        except Exception:  # noqa: BLE001
            return
        with self._lock:
            self._buf.append(data)
            self._buffered += len(data)
            max_samples = int(_MAX_BUFFER_SECONDS * self._rate)
            while self._buffered > max_samples and len(self._buf) > 1:
                self._buffered -= len(self._buf.popleft())

    def _open(self, frame) -> None:
        self._rate = frame.sample_rate or 48000
        self._channels = len(frame.layout.channels) or 2
        layout = "stereo" if self._channels == 2 else "mono"
        self._resampler = av.AudioResampler(format="flt", layout=layout, rate=self._rate)
        self._stream = sd.OutputStream(
            samplerate=self._rate,
            channels=self._channels,
            dtype="float32",
            callback=self._callback,
        )
        self._stream.start()

    def _callback(self, outdata, frames, time_info, status) -> None:  # sounddevice cb
        idx = 0
        with self._lock:
            while frames > 0 and self._buf:
                chunk = self._buf[0]
                take = min(len(chunk), frames)
                outdata[idx:idx + take] = chunk[:take]
                if take == len(chunk):
                    self._buf.popleft()
                else:
                    self._buf[0] = chunk[take:]
                self._buffered -= take
                idx += take
                frames -= take
        if frames > 0:
            outdata[idx:] = 0  # underflow -> silence

    def stop(self) -> None:
        with self._lock:
            self._buf.clear()
            self._buffered = 0
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:  # noqa: BLE001
                pass
        self._stream = None
        self._resampler = None
