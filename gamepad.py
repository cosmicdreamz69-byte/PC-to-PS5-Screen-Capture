"""PC gamepad -> Remote Play controller bridge (Play mode).

Reads a controller plugged into the PC via SDL's game-controller API (which
normalizes a DualSense/Xbox pad regardless of USB/Bluetooth) and forwards input
to the console over the Remote Play session. Because you're controlling the
console *remotely*, you never trigger a local login -- so the stream stays up
and there's no controller-off loop.

poll() is meant to be called ~60x/sec from the GUI (Qt) thread. pyremoteplay's
Controller.button()/stick()/update_sticks() only enqueue state and send a UDP
feedback packet, so calling them from this thread is fine.
"""
from __future__ import annotations

import os

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

import pygame  # noqa: E402

try:
    import pygame._sdl2.controller as sdlcontroller  # noqa: E402
    _HAVE_SDL2 = True
except Exception:  # noqa: BLE001
    sdlcontroller = None
    _HAVE_SDL2 = False

# SDL game-controller button -> Remote Play button name
BUTTON_MAP = {
    pygame.CONTROLLER_BUTTON_A: "CROSS",
    pygame.CONTROLLER_BUTTON_B: "CIRCLE",
    pygame.CONTROLLER_BUTTON_X: "SQUARE",
    pygame.CONTROLLER_BUTTON_Y: "TRIANGLE",
    pygame.CONTROLLER_BUTTON_DPAD_UP: "UP",
    pygame.CONTROLLER_BUTTON_DPAD_DOWN: "DOWN",
    pygame.CONTROLLER_BUTTON_DPAD_LEFT: "LEFT",
    pygame.CONTROLLER_BUTTON_DPAD_RIGHT: "RIGHT",
    pygame.CONTROLLER_BUTTON_LEFTSHOULDER: "L1",
    pygame.CONTROLLER_BUTTON_RIGHTSHOULDER: "R1",
    pygame.CONTROLLER_BUTTON_LEFTSTICK: "L3",
    pygame.CONTROLLER_BUTTON_RIGHTSTICK: "R3",
    pygame.CONTROLLER_BUTTON_START: "OPTIONS",
    pygame.CONTROLLER_BUTTON_BACK: "SHARE",
    pygame.CONTROLLER_BUTTON_GUIDE: "PS",
}
_TOUCHPAD = getattr(pygame, "CONTROLLER_BUTTON_TOUCHPAD", None)
if _TOUCHPAD is not None:
    BUTTON_MAP[_TOUCHPAD] = "TOUCHPAD"

DEADZONE = 0.12
TRIGGER_THRESHOLD = 0.5
AXIS_MAX = 32767.0


class GamepadBridge:
    def __init__(self, controller_getter) -> None:
        # controller_getter() -> the live pyremoteplay Controller, or None
        self._get_rp = controller_getter
        self._pad = None
        self._name = ""
        self._active = False
        self._btn_state = {}      # rp_name -> bool
        self._l2 = False
        self._r2 = False
        self._last_sticks = (0.0, 0.0, 0.0, 0.0)

    @property
    def name(self) -> str:
        return self._name

    @property
    def connected(self) -> bool:
        return self._pad is not None

    def start(self) -> None:
        if not pygame.get_init():
            pygame.init()
        if _HAVE_SDL2:
            try:
                sdlcontroller.init()
            except Exception:  # noqa: BLE001
                pass
        self._active = True

    def _ensure_pad(self) -> bool:
        if self._pad is not None:
            return True
        if not _HAVE_SDL2:
            return False
        try:
            if sdlcontroller.get_count() > 0:
                self._pad = sdlcontroller.Controller(0)
                self._name = self._pad.name or "Controller"
                return True
        except Exception:  # noqa: BLE001
            self._pad = None
        return False

    def poll(self) -> None:
        """Call ~60Hz from the GUI thread."""
        if not self._active:
            return
        pygame.event.pump()
        rp = self._get_rp()
        if rp is None or not self._ensure_pad():
            return
        pad = self._pad
        # face/dpad/shoulder/stick-press/system buttons
        for sdl_btn, rp_name in BUTTON_MAP.items():
            try:
                pressed = bool(pad.get_button(sdl_btn))
            except Exception:  # noqa: BLE001
                continue
            if pressed != self._btn_state.get(rp_name, False):
                self._btn_state[rp_name] = pressed
                self._send_button(rp, rp_name, pressed)
        # analog triggers -> digital L2/R2
        self._trigger(rp, pad, pygame.CONTROLLER_AXIS_TRIGGERLEFT, "L2", "_l2")
        self._trigger(rp, pad, pygame.CONTROLLER_AXIS_TRIGGERRIGHT, "R2", "_r2")
        # sticks (SDL up = -1, which matches Remote Play's convention)
        lx = self._axis(pad, pygame.CONTROLLER_AXIS_LEFTX)
        ly = self._axis(pad, pygame.CONTROLLER_AXIS_LEFTY)
        rx = self._axis(pad, pygame.CONTROLLER_AXIS_RIGHTX)
        ry = self._axis(pad, pygame.CONTROLLER_AXIS_RIGHTY)
        cur = (round(lx, 3), round(ly, 3), round(rx, 3), round(ry, 3))
        if cur != self._last_sticks:
            self._last_sticks = cur
            try:
                rp.stick("left", point=(lx, ly))
                rp.stick("right", point=(rx, ry))
                rp.update_sticks()
            except Exception:  # noqa: BLE001
                pass

    def _axis(self, pad, axis) -> float:
        try:
            v = pad.get_axis(axis) / AXIS_MAX
        except Exception:  # noqa: BLE001
            return 0.0
        if -DEADZONE < v < DEADZONE:
            return 0.0
        return max(-1.0, min(1.0, v))

    def _trigger(self, rp, pad, axis, rp_name, attr) -> None:
        try:
            v = pad.get_axis(axis) / AXIS_MAX
        except Exception:  # noqa: BLE001
            return
        pressed = v > TRIGGER_THRESHOLD
        if pressed != getattr(self, attr):
            setattr(self, attr, pressed)
            self._send_button(rp, rp_name, pressed)

    @staticmethod
    def _send_button(rp, name, pressed) -> None:
        try:
            rp.button(name, "press" if pressed else "release")
        except Exception:  # noqa: BLE001
            pass

    def release_all(self) -> None:
        """Release everything (so nothing sticks when Play mode turns off)."""
        rp = self._get_rp()
        if rp is not None:
            for name, pressed in list(self._btn_state.items()):
                if pressed:
                    self._send_button(rp, name, False)
            for attr, name in (("_l2", "L2"), ("_r2", "R2")):
                if getattr(self, attr):
                    self._send_button(rp, name, False)
            try:
                rp.stick("left", point=(0.0, 0.0))
                rp.stick("right", point=(0.0, 0.0))
                rp.update_sticks()
            except Exception:  # noqa: BLE001
                pass
        self._btn_state.clear()
        self._l2 = self._r2 = False
        self._last_sticks = (0.0, 0.0, 0.0, 0.0)

    def stop(self) -> None:
        self.release_all()
        self._active = False
        self._pad = None
