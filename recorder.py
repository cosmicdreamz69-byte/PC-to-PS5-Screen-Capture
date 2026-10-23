"""Record the incoming stream to an MP4 file, with optional audio.

Video and audio arrive on the stream thread as decoded ``av`` frames. We
re-encode video with libx264 and (optionally) audio with AAC, and mux to MP4.

Timing: video frames are stamped with their wall-clock arrival time (so a
variable/stuttery stream plays back at real speed, and a reconnect gap shows as
a short freeze rather than a speed-up). Audio is stamped by cumulative sample
count from the same zero point, which keeps A/V roughly in sync.

The container isn't opened until we have the first video frame (for dimensions)
and, if recording audio, the first audio frame (for rate/channels) -- MP4 needs
all streams declared before the first packet. If audio never shows up we fall
back to video-only after a short grace period. Any error in the audio path
disables audio for the rest of the file instead of corrupting it.
"""
from __future__ import annotations

import threading
import time
from fractions import Fraction
from typing import List, Optional, Tuple

import av

VIDEO_TB = Fraction(1, 90000)   # standard 90kHz video time base
AAC_FRAME = 1024                # AAC encoder's fixed frame size
AUDIO_GRACE = 1.5               # seconds to wait for audio before going video-only


def _pick_video_encoder() -> str:
    for name in ("libx264", "mpeg4"):
        try:
            av.codec.Codec(name, "w")
            return name
        except Exception:  # noqa: BLE001
            continue
    return "mpeg4"


class Recorder:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._venc = _pick_video_encoder()
        self._reset()

    def _reset(self) -> None:
        self._active = False
        self._ready = False
        self._container = None
        self._vstream = None
        self._astream = None
        self._aresampler = None
        self._afifo = None
        self._path: Optional[str] = None
        self._fps = 60
        self._want_audio = False
        self._audio_ok = False
        self._t0: Optional[float] = None
        self._first_v_time: Optional[float] = None
        self._a_pts = 0
        self._frame_count = 0
        self._buf_v: List[Tuple[object, float]] = []
        self._buf_a: List[object] = []

    # -- state ----------------------------------------------------------------
    @property
    def active(self) -> bool:
        return self._active

    @property
    def frame_count(self) -> int:
        return self._frame_count

    @property
    def elapsed(self) -> float:
        return (time.monotonic() - self._t0) if (self._active and self._t0) else 0.0

    # -- control --------------------------------------------------------------
    def start(self, path: str, fps: int = 60, record_audio: bool = True) -> None:
        with self._lock:
            if self._active:
                return
            self._reset()
            self._path = path
            self._fps = max(1, int(fps))
            self._want_audio = bool(record_audio)
            self._active = True

    def stop(self) -> Optional[str]:
        with self._lock:
            if not self._active:
                return None
            self._active = False
            path = self._path
            try:
                if self._ready and self._container is not None:
                    # drain the audio fifo, then flush both encoders
                    if self._audio_ok and self._afifo is not None:
                        self._drain_fifo(final=True)
                    if self._vstream is not None:
                        for p in self._vstream.encode():
                            self._container.mux(p)
                    if self._audio_ok and self._astream is not None:
                        for p in self._astream.encode():
                            self._container.mux(p)
            except Exception:  # noqa: BLE001
                pass
            finally:
                if self._container is not None:
                    try:
                        self._container.close()
                    except Exception:  # noqa: BLE001
                        pass
            wrote = self._frame_count
            self._container = None
            return path if wrote else None

    # -- frame ingestion ------------------------------------------------------
    def write_video(self, frame) -> None:
        with self._lock:
            if not self._active:
                return
            now = time.monotonic()
            if self._first_v_time is None:
                self._first_v_time = now
            if not self._ready:
                self._buf_v.append((frame, now))
                self._try_open(now)
                if not self._ready:
                    return
                self._flush_buffers()
                return
            self._encode_video(frame, now)

    def write_audio(self, frame) -> None:
        with self._lock:
            if not self._active or not self._want_audio:
                return
            if not self._ready:
                if len(self._buf_a) < 64:
                    self._buf_a.append(frame)
                return
            if self._audio_ok:
                self._encode_audio(frame)

    # -- container setup ------------------------------------------------------
    def _try_open(self, now: float) -> None:
        if not self._buf_v:
            return
        have_audio = bool(self._buf_a)
        waited = now - (self._first_v_time or now)
        if self._want_audio and not have_audio and waited < AUDIO_GRACE:
            return  # keep waiting for the first audio frame

        vframe0 = self._buf_v[0][0]
        self._t0 = self._buf_v[0][1]
        self._container = av.open(self._path, mode="w")

        vs = self._container.add_stream(self._venc, rate=self._fps)
        vs.width = vframe0.width
        vs.height = vframe0.height
        vs.pix_fmt = "yuv420p"
        vs.codec_context.time_base = VIDEO_TB
        if self._venc == "libx264":
            vs.options = {"preset": "veryfast", "crf": "20"}
        self._vstream = vs

        if self._want_audio and have_audio:
            self._setup_audio(self._buf_a[0])

        self._ready = True

    def _setup_audio(self, aframe) -> None:
        try:
            rate = aframe.sample_rate or 48000
            channels = len(aframe.layout.channels) or 2
            layout = "stereo" if channels == 2 else "mono"
            a = self._container.add_stream("aac", rate=rate)
            a.codec_context.time_base = Fraction(1, rate)
            # encoders are picky about sample format / channel layout; set both.
            for attr, val in (("format", "fltp"), ("layout", layout)):
                try:
                    setattr(a.codec_context, attr, val)
                except Exception:  # noqa: BLE001
                    pass
            try:
                a.bit_rate = 160000
            except Exception:  # noqa: BLE001
                pass
            self._astream = a
            self._arate = rate
            self._aresampler = av.AudioResampler(format="fltp", layout=layout, rate=rate)
            self._afifo = av.AudioFifo()
            self._a_pts = 0
            self._audio_ok = True
        except Exception:  # noqa: BLE001
            self._audio_ok = False

    def _flush_buffers(self) -> None:
        for frame, t in self._buf_v:
            self._encode_video(frame, t)
        self._buf_v = []
        if self._audio_ok:
            for frame in self._buf_a:
                self._encode_audio(frame)
        self._buf_a = []

    # -- encoding -------------------------------------------------------------
    def _encode_video(self, frame, t: float) -> None:
        try:
            vframe = frame.reformat(
                width=self._vstream.width,
                height=self._vstream.height,
                format="yuv420p",
            )
            vframe.pts = int(round((t - self._t0) / VIDEO_TB))
            for p in self._vstream.encode(vframe):
                self._container.mux(p)
            self._frame_count += 1
        except Exception:  # noqa: BLE001
            pass

    def _encode_audio(self, frame) -> None:
        try:
            out = self._aresampler.resample(frame)
            frames = out if isinstance(out, list) else [out]
            for fr in frames:
                if fr is not None:
                    self._afifo.write(fr)
            self._drain_fifo(final=False)
        except Exception:  # noqa: BLE001 - give up on audio, keep video going
            self._audio_ok = False

    def _drain_fifo(self, final: bool) -> None:
        while True:
            chunk = self._afifo.read(AAC_FRAME, partial=final)
            if chunk is None:
                break
            chunk.pts = self._a_pts
            chunk.time_base = Fraction(1, self._arate)
            self._a_pts += chunk.samples
            for p in self._astream.encode(chunk):
                self._container.mux(p)
            if final and chunk.samples < AAC_FRAME:
                break
