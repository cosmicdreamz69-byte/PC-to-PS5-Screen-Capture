"""Tiny JSON config so the app remembers your last console IP and settings."""
from __future__ import annotations

import json
import os
from typing import Any, Dict

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(APP_DIR, "config.json")

DEFAULTS: Dict[str, Any] = {
    "last_ip": "192.168.20.38",
    "resolution": "1080p",
    "fps": 60,
    "codec": "h264",
    "play_audio": True,      # live audio through the PC speakers
    "record_audio": True,    # mux audio into recordings
    "auto_reconnect": True,  # recover automatically when the stream drops
}


def load() -> Dict[str, Any]:
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            cfg.update({k: data[k] for k in DEFAULTS if k in data})
    except (OSError, ValueError):
        pass
    return cfg


def save(cfg: Dict[str, Any]) -> None:
    out = {k: cfg.get(k, DEFAULTS[k]) for k in DEFAULTS}
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=2)
    except OSError:
        pass
