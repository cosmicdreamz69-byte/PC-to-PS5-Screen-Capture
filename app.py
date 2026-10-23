"""PS5 Screen Capture - GUI.

Live viewer + recorder for the PS5 screen over the Remote Play protocol. Shows
the console screen, plays its audio, records to MP4 (with audio), and grabs PNG
screenshots. It auto-reconnects when the stream drops (e.g. when a controller
reconnecting to the console interrupts the session). No controller input is ever
sent -- capture only.

Run:  python -m ps5capture.app
"""
from __future__ import annotations

import datetime as _dt
import os
import threading
import time

import numpy as np
from PySide6.QtCore import Qt, QObject, Signal, QTimer
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import config
from .gamepad import GamepadBridge
from .session import CaptureController

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REC_DIR = os.path.join(APP_DIR, "recordings")
SHOT_DIR = os.path.join(APP_DIR, "screenshots")

STALL_SECONDS = 3.5          # no frames for this long -> treat as dropped
RECONNECT_ATTEMPTS = 6

STYLE = """
QWidget { background:#15161a; color:#e7e8ea; font-family:'Segoe UI','Arial'; font-size:13px; }
#card { background:#1c1e24; border:1px solid #2a2d35; border-radius:10px; }
QLineEdit, QComboBox { background:#23252b; border:1px solid #33363d; border-radius:6px; padding:6px 9px; }
QComboBox::drop-down { border:0; width:18px; }
QComboBox QAbstractItemView { background:#23252b; selection-background-color:#3b82f6; }
QPushButton { background:#2b2e36; border:1px solid #3a3e47; border-radius:7px; padding:8px 16px; }
QPushButton:hover { background:#343843; }
QPushButton:disabled { color:#5a5d63; background:#212329; border-color:#2a2d35; }
QPushButton#primary { background:#3b82f6; border:0; color:white; font-weight:600; }
QPushButton#primary:hover { background:#4b8ef7; }
QPushButton#record { background:#ef4444; border:0; color:white; font-weight:600; }
QPushButton#record:hover { background:#f15a5a; }
#video { background:#000; border-radius:10px; color:#6b6e76; }
#title { font-size:16px; font-weight:700; letter-spacing:0.3px; }
#pill { border-radius:11px; padding:3px 12px; font-size:12px; font-weight:600; color:white; }
#muted { color:#9a9da3; }
QCheckBox { spacing:6px; }
QCheckBox::indicator { width:16px; height:16px; border-radius:4px; border:1px solid #3a3e47; background:#23252b; }
QCheckBox::indicator:checked { background:#3b82f6; border-color:#3b82f6; }
"""

_PILL = {
    "idle": ("#3a3e47", "Idle"),
    "connecting": ("#d97706", "Connecting…"),
    "live": ("#16a34a", "● Live"),
    "reconnecting": ("#d97706", "Reconnecting…"),
    "error": ("#dc2626", "Disconnected"),
}


def _ts() -> str:
    return _dt.datetime.now().strftime("%Y%m%d_%H%M%S")


class _Bridge(QObject):
    conn_result = Signal(bool, str)
    reconnecting = Signal()
    reconnected = Signal(bool, str)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.cfg = config.load()
        self.setWindowTitle("PS5 Screen Capture")
        self.resize(1120, 720)
        self.setMinimumSize(760, 480)

        self.controller: CaptureController | None = None
        self.bridge = _Bridge()
        self.bridge.conn_result.connect(self._on_conn_result)
        self.bridge.reconnecting.connect(self._on_reconnecting)
        self.bridge.reconnected.connect(self._on_reconnected)

        self._latest: QImage | None = None
        self._latest_lock = threading.Lock()
        self._last_frame = 0.0
        self._reconnecting = False
        self._cur_fps = int(self.cfg["fps"])

        # Play mode: forward a PC-connected controller to the console
        self.gamepad = GamepadBridge(
            lambda: self.controller.rp_controller()
            if (self.controller and self.controller.connected) else None
        )

        self._build_ui()
        self._apply_cfg()

        self._pad_timer = QTimer(self)
        self._pad_timer.timeout.connect(self.gamepad.poll)

        self._paint_timer = QTimer(self)
        self._paint_timer.timeout.connect(self._repaint_video)
        self._paint_timer.start(16)  # ~60 Hz

        self._ui_timer = QTimer(self)
        self._ui_timer.timeout.connect(self._tick)
        self._ui_timer.start(500)

    # -- UI construction ------------------------------------------------------
    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        # header
        header = QHBoxLayout()
        title = QLabel("PS5 Screen Capture")
        title.setObjectName("title")
        self.pill = QLabel()
        self.pill.setObjectName("pill")
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.pill)
        root.addLayout(header)

        # connection card
        card = QFrame()
        card.setObjectName("card")
        conn = QHBoxLayout(card)
        conn.setContentsMargins(12, 10, 12, 10)
        conn.setSpacing(8)
        self.ip_edit = QLineEdit()
        self.ip_edit.setFixedWidth(150)
        self.res_box = QComboBox()
        self.res_box.addItems(["540p", "720p", "1080p"])
        self.fps_box = QComboBox()
        self.fps_box.addItems(["30", "60"])
        self.codec_box = QComboBox()
        self.codec_box.addItems(["h264", "hevc"])
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.setObjectName("primary")
        self.connect_btn.clicked.connect(self._toggle_connect)
        for lbl, w in (
            ("PS5 IP", self.ip_edit),
            ("Res", self.res_box),
            ("FPS", self.fps_box),
            ("Codec", self.codec_box),
        ):
            conn.addWidget(QLabel(lbl))
            conn.addWidget(w)
        conn.addStretch(1)
        conn.addWidget(self.connect_btn)
        root.addWidget(card)

        # video
        self.video = QLabel("Not connected")
        self.video.setObjectName("video")
        self.video.setAlignment(Qt.AlignCenter)
        self.video.setMinimumSize(640, 360)
        root.addWidget(self.video, stretch=1)

        # bottom toolbar
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.record_btn = QPushButton("● Record")
        self.record_btn.clicked.connect(self._toggle_record)
        self.record_btn.setEnabled(False)
        self.shot_btn = QPushButton("Screenshot")
        self.shot_btn.clicked.connect(self._screenshot)
        self.shot_btn.setEnabled(False)
        self.audio_box = QCheckBox("🔊 Audio")
        self.audio_box.toggled.connect(self._on_audio_toggle)
        self.rec_audio_box = QCheckBox("Record audio")
        self.auto_box = QCheckBox("Auto-reconnect")
        self.play_box = QCheckBox("🎮 Play mode")
        self.play_box.toggled.connect(self._on_play_toggle)
        self.pad_label = QLabel("")
        self.pad_label.setObjectName("muted")
        self.rec_label = QLabel("")
        self.rec_label.setObjectName("muted")
        bar.addWidget(self.record_btn)
        bar.addWidget(self.shot_btn)
        bar.addSpacing(10)
        bar.addWidget(self.audio_box)
        bar.addWidget(self.rec_audio_box)
        bar.addWidget(self.auto_box)
        bar.addWidget(self.play_box)
        bar.addWidget(self.pad_label)
        bar.addStretch(1)
        bar.addWidget(self.rec_label)
        root.addLayout(bar)

        # status line
        self.status = QLabel("Capture only — no controller input is sent.")
        self.status.setObjectName("muted")
        root.addWidget(self.status)

        self.setCentralWidget(central)
        self._set_state("idle")

    def _apply_cfg(self) -> None:
        self.ip_edit.setText(str(self.cfg["last_ip"]))
        self.res_box.setCurrentText(str(self.cfg["resolution"]))
        self.fps_box.setCurrentText(str(self.cfg["fps"]))
        self.codec_box.setCurrentText(str(self.cfg["codec"]))
        self.audio_box.setChecked(bool(self.cfg["play_audio"]))
        self.rec_audio_box.setChecked(bool(self.cfg["record_audio"]))
        self.auto_box.setChecked(bool(self.cfg["auto_reconnect"]))

    def _save_cfg(self) -> None:
        self.cfg.update(
            last_ip=self.ip_edit.text().strip() or self.cfg["last_ip"],
            resolution=self.res_box.currentText(),
            fps=int(self.fps_box.currentText()),
            codec=self.codec_box.currentText(),
            play_audio=self.audio_box.isChecked(),
            record_audio=self.rec_audio_box.isChecked(),
            auto_reconnect=self.auto_box.isChecked(),
        )
        config.save(self.cfg)

    # -- status pill / text ---------------------------------------------------
    def _set_state(self, state: str) -> None:
        color, text = _PILL.get(state, _PILL["idle"])
        self.pill.setText(text)
        self.pill.setStyleSheet(f"#pill {{ background:{color}; }}")

    def _set_status(self, text: str) -> None:
        self.status.setText(text)

    # -- connect --------------------------------------------------------------
    def _toggle_connect(self) -> None:
        if self.controller and self.controller.connected:
            self._do_disconnect()
            return
        ip = self.ip_edit.text().strip() or str(self.cfg["last_ip"])
        self._cur_fps = int(self.fps_box.currentText())
        res = self.res_box.currentText()
        codec = self.codec_box.currentText()
        play_audio = self.audio_box.isChecked()
        self.connect_btn.setEnabled(False)
        self._set_state("connecting")
        self._set_status(f"Connecting to {ip} …")

        def worker() -> None:
            try:
                ctl = CaptureController(ip)
                ctl.on_frame = self._on_frame
                ok, msg = ctl.connect(
                    resolution=res, fps=self._cur_fps, codec=codec,
                    play_audio=play_audio,
                )
                if ok:
                    self.controller = ctl
                else:
                    ctl.shutdown()
                self.bridge.conn_result.emit(ok, msg)
            except Exception as exc:  # noqa: BLE001
                self.bridge.conn_result.emit(False, f"Error: {exc}")

        threading.Thread(target=worker, daemon=True).start()

    def _on_conn_result(self, ok: bool, msg: str) -> None:
        self.connect_btn.setEnabled(True)
        self._set_status(msg)
        if ok:
            self._last_frame = time.monotonic()
            self.connect_btn.setText("Disconnect")
            self.record_btn.setEnabled(True)
            self.shot_btn.setEnabled(True)
            self._set_state("live")
            self._save_cfg()
        else:
            self.connect_btn.setText("Connect")
            self._set_state("error")

    def _do_disconnect(self) -> None:
        if self.controller:
            if self.controller.recorder.active:
                self._toggle_record()
            self.controller.disconnect()
        self.connect_btn.setText("Connect")
        self.record_btn.setEnabled(False)
        self.shot_btn.setEnabled(False)
        self._set_state("idle")
        self._set_status("Disconnected.")
        with self._latest_lock:
            self._latest = None
        self.video.setPixmap(QPixmap())
        self.video.setText("Not connected")

    # -- auto-reconnect -------------------------------------------------------
    def _maybe_reconnect(self) -> None:
        if not (self.controller and self.controller.connected):
            return
        if self._reconnecting or not self.auto_box.isChecked():
            return
        if self._last_frame and (time.monotonic() - self._last_frame) > STALL_SECONDS:
            self._reconnecting = True
            self.bridge.reconnecting.emit()

            def worker() -> None:
                ok, msg = False, "Reconnect failed."
                for _ in range(RECONNECT_ATTEMPTS):
                    try:
                        ok, msg = self.controller.reconnect()
                    except Exception as exc:  # noqa: BLE001
                        ok, msg = False, f"Reconnect error: {exc}"
                    if ok:
                        break
                    time.sleep(2)
                self.bridge.reconnected.emit(ok, msg)

            threading.Thread(target=worker, daemon=True).start()

    def _on_reconnecting(self) -> None:
        self._set_state("reconnecting")
        self._set_status("Stream dropped — reconnecting…")

    def _on_reconnected(self, ok: bool, msg: str) -> None:
        self._reconnecting = False
        if ok:
            self._last_frame = time.monotonic()
            self._set_state("live")
            self._set_status("Reconnected. " + msg)
        else:
            self._set_state("error")
            self._set_status(msg + " Click Connect to retry.")
            self.connect_btn.setText("Connect")
            self.record_btn.setEnabled(False)
            self.shot_btn.setEnabled(False)

    # -- frames ---------------------------------------------------------------
    def _on_frame(self, frame) -> None:
        try:
            arr = frame.to_ndarray(format="rgb24")
            h, w, _ = arr.shape
            img = QImage(arr.data, w, h, 3 * w, QImage.Format_RGB888).copy()
        except Exception:  # noqa: BLE001
            return
        with self._latest_lock:
            self._latest = img
            self._last_frame = time.monotonic()

    def _repaint_video(self) -> None:
        with self._latest_lock:
            img = self._latest
        if img is None:
            return
        pix = QPixmap.fromImage(img).scaled(
            self.video.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        self.video.setPixmap(pix)

    # -- recording ------------------------------------------------------------
    def _toggle_record(self) -> None:
        if not self.controller:
            return
        if self.controller.recorder.active:
            path = self.controller.stop_recording()
            self.record_btn.setText("● Record")
            self.record_btn.setObjectName("")
            self._repolish(self.record_btn)
            self.rec_label.setText("")
            self._set_status(
                f"Saved recording: {path}" if path
                else "Recording stopped (no frames captured)."
            )
        else:
            os.makedirs(REC_DIR, exist_ok=True)
            path = os.path.join(REC_DIR, f"PS5_{_ts()}.mp4")
            self.controller.start_recording(
                path, fps=self._cur_fps, record_audio=self.rec_audio_box.isChecked()
            )
            self.record_btn.setText("■ Stop")
            self.record_btn.setObjectName("record")
            self._repolish(self.record_btn)
            self._set_status(f"Recording → {path}")

    def _repolish(self, w: QWidget) -> None:
        w.style().unpolish(w)
        w.style().polish(w)

    # -- audio ----------------------------------------------------------------
    def _on_audio_toggle(self, checked: bool) -> None:
        if self.controller:
            self.controller.set_audio_muted(not checked)

    # -- play mode (controller via PC) ----------------------------------------
    def _on_play_toggle(self, checked: bool) -> None:
        if checked:
            self.gamepad.start()
            self._pad_timer.start(16)  # ~60 Hz input poll
            self.pad_label.setText("🎮 searching…")
            self._set_status(
                "Play mode ON — a controller connected to this PC now drives the "
                "console. (No input is sent to the console otherwise.)"
            )
        else:
            self._pad_timer.stop()
            self.gamepad.stop()
            self.pad_label.setText("")
            self._set_status("Play mode off.")

    # -- screenshot -----------------------------------------------------------
    def _screenshot(self) -> None:
        with self._latest_lock:
            img = self._latest.copy() if self._latest is not None else None
        if img is None:
            self._set_status("No frame to capture yet.")
            return
        os.makedirs(SHOT_DIR, exist_ok=True)
        path = os.path.join(SHOT_DIR, f"PS5_{_ts()}.png")
        img.save(path, "PNG")
        self._set_status(f"Saved screenshot: {path}")

    # -- periodic UI tick -----------------------------------------------------
    def _tick(self) -> None:
        self._maybe_reconnect()
        if self.play_box.isChecked():
            self.pad_label.setText(
                f"🎮 {self.gamepad.name}" if self.gamepad.connected
                else "🎮 no controller — connect one to this PC"
            )
        if self.controller and self.controller.recorder.active:
            r = self.controller.recorder
            self.rec_label.setText(f"REC  {int(r.elapsed)}s  ·  {r.frame_count} frames")

    # -- lifecycle ------------------------------------------------------------
    def closeEvent(self, event) -> None:  # noqa: N802
        self._save_cfg()
        self._pad_timer.stop()
        self.gamepad.stop()
        if self.controller:
            self.controller.shutdown()
        super().closeEvent(event)


def main() -> int:
    app = QApplication([])
    app.setStyleSheet(STYLE)
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
