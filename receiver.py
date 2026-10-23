"""Video/audio receiver.

pyremoteplay decodes the console's hardware-encoded stream and calls
``handle_video`` as soon as each frame is ready. We subclass the abstract
``AVReceiver`` and forward frames to a callback -- that's the only hook the
capture app needs. We never touch the input/controller side, so nothing is ever
sent back to the console: this is pure screen capture.
"""
from __future__ import annotations

from typing import Callable, Optional

from pyremoteplay.receiver import AVReceiver


class CaptureReceiver(AVReceiver):
    """An AVReceiver that pushes decoded frames to user-supplied callbacks."""

    def __init__(
        self,
        on_video: Optional[Callable] = None,
        on_audio: Optional[Callable] = None,
    ) -> None:
        super().__init__()
        self._on_video = on_video
        self._on_audio = on_audio

    def handle_video(self, frame) -> None:  # frame: av.VideoFrame
        if self._on_video is not None:
            try:
                self._on_video(frame)
            except Exception:  # noqa: BLE001 - never let a callback kill the stream
                pass

    def handle_audio(self, frame) -> None:  # frame: av.AudioFrame
        if self._on_audio is not None:
            try:
                self._on_audio(frame)
            except Exception:  # noqa: BLE001
                pass
