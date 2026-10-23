"""Session controller.

Owns the RPDevice and a private asyncio loop (in its own thread), and exposes a
simple threaded API for the GUI: connect / disconnect / reconnect, recording,
and live audio. Frames are fanned out to the GUI, the recorder, and the audio
player. No controller/button input is ever created or sent -- capture only.
"""
from __future__ import annotations

import asyncio
import threading
import time
from typing import Callable, List, Optional, Tuple

from pyremoteplay import RPDevice

from .audio import AudioPlayer
from .receiver import CaptureReceiver
from .recorder import Recorder


class CaptureController:
    WAKE_TIMEOUT = 45  # seconds to wait for a console to leave standby

    def __init__(self, host: str) -> None:
        self.host = host
        self.device = RPDevice(host)
        self.recorder = Recorder()
        self.audio = AudioPlayer()
        self.on_frame: Optional[Callable] = None  # set by the GUI

        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop, name="rp-asyncio", daemon=True
        )
        self._thread.start()
        self._connected = False
        self._params: dict = {}  # last successful connect params, for reconnect

    # -- asyncio loop ---------------------------------------------------------
    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    # -- status / users -------------------------------------------------------
    def get_status(self) -> Optional[dict]:
        return self.device.get_status()

    @property
    def status_name(self) -> str:
        return self.device.status_name or "Unknown"

    @property
    def is_on(self) -> bool:
        return bool(self.device.is_on)

    @property
    def connected(self) -> bool:
        return self._connected

    def registered_users(self) -> List[str]:
        return list(self.device.get_users() or [])

    # -- frame / audio fan-out ------------------------------------------------
    def _handle_video(self, frame) -> None:
        if self.recorder.active:
            self.recorder.write_video(frame)
        if self.on_frame is not None:
            self.on_frame(frame)

    def _handle_audio(self, frame) -> None:
        self.audio.feed(frame)
        if self.recorder.active:
            self.recorder.write_audio(frame)

    def set_audio_muted(self, muted: bool) -> None:
        self.audio.set_muted(muted)

    def rp_controller(self):
        """The live pyremoteplay Controller for input forwarding (Play mode),
        or None if there's no session yet."""
        try:
            return self.device.controller
        except Exception:  # noqa: BLE001
            return None

    # -- connect --------------------------------------------------------------
    def connect(
        self,
        user: Optional[str] = None,
        resolution: str = "1080p",
        fps: int = 60,
        codec: str = "h264",
        play_audio: bool = True,
        wake_if_needed: bool = True,
    ) -> Tuple[bool, str]:
        """Establish the stream. Call from a worker thread (blocks until up or
        failed). Returns (ok, message)."""
        status = self.device.get_status()
        if not status:
            return False, (
                "Can't reach the console. Check the IP, same-LAN, and that "
                "Remote Play is enabled."
            )

        users = self.registered_users()
        if not users:
            return False, (
                "This PC isn't linked to the console yet. Run registration "
                "first (register.bat)."
            )
        user = user or users[0]

        if not self.device.is_on:
            if not wake_if_needed:
                return False, "Console is in standby. Wake it and try again."
            self.device.wakeup(user)
            deadline = time.monotonic() + self.WAKE_TIMEOUT
            while time.monotonic() < deadline:
                time.sleep(2)
                self.device.get_status()
                if self.device.is_on:
                    break
            else:
                return False, "Console did not wake in time. Try again."

        self.audio.set_muted(not play_audio)
        receiver = CaptureReceiver(
            on_video=self._handle_video, on_audio=self._handle_audio
        )

        async def _start() -> bool:
            session = self.device.create_session(
                user,
                loop=self._loop,
                receiver=receiver,
                resolution=resolution,
                fps=fps,
                codec=codec,
            )
            if session is None:
                return False
            return await self.device.connect()

        try:
            fut = asyncio.run_coroutine_threadsafe(_start(), self._loop)
            ok = bool(fut.result(timeout=40))
        except Exception as exc:  # noqa: BLE001
            return False, f"Connection error: {exc}"

        self._connected = ok
        if not ok:
            return False, (
                "Session failed to start. Make sure no other Remote Play client "
                "is connected, then retry."
            )
        self._params = dict(
            user=user, resolution=resolution, fps=fps, codec=codec,
            play_audio=play_audio,
        )
        return True, f"Streaming from {self.host} ({resolution} @ {fps}fps)."

    def reconnect(self) -> Tuple[bool, str]:
        """Re-establish the stream using the last params, WITHOUT stopping an
        in-progress recording (the file just gets a short gap)."""
        if not self._params:
            return False, "Nothing to reconnect to."
        try:
            self.device.disconnect()
        except Exception:  # noqa: BLE001
            pass
        self.audio.stop()  # reopens on the next audio frame
        self._connected = False
        p = self._params
        return self.connect(
            user=p.get("user"),
            resolution=p["resolution"],
            fps=p["fps"],
            codec=p["codec"],
            play_audio=p.get("play_audio", True),
            wake_if_needed=True,
        )

    def disconnect(self) -> None:
        self._connected = False
        if self.recorder.active:
            self.recorder.stop()
        self.audio.stop()
        try:
            self.device.disconnect()
        except Exception:  # noqa: BLE001
            pass

    # -- recording ------------------------------------------------------------
    def start_recording(self, path: str, fps: int = 60, record_audio: bool = True) -> None:
        self.recorder.start(path, fps=fps, record_audio=record_audio)

    def stop_recording(self) -> Optional[str]:
        return self.recorder.stop()

    # -- shutdown -------------------------------------------------------------
    def shutdown(self) -> None:
        self.disconnect()
        self._loop.call_soon_threadsafe(self._loop.stop)
