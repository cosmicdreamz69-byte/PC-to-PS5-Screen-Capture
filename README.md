# PS5 Screen Capture

A **video-only Remote Play client**. It uses the PlayStation's own hardware
video encoder and the Remote Play streaming protocol (via
[`pyremoteplay`](https://pypi.org/project/pyremoteplay/)) to pull the live
console screen to your PC — but it **never sends controller input**. It's Remote
Play with the "remote control" half removed: pure screen capture, live preview,
MP4 recording, and PNG screenshots.

Works on both PS4 and PS5 (it's the Remote Play protocol, so it does **not**
require a jailbreak — just Remote Play enabled and a PSN account).

## How it compares to a capture card

| | This app (Remote Play) | HDMI capture card |
|---|---|---|
| Hardware | None | ~$20 USB dongle |
| Game/HDCP content | Streams fine (Sony-sanctioned path) | Needs an HDCP-strip card for some apps |
| Quality | Up to 1080p60, console-encoded | Up to source resolution, lowest latency |
| Setup | PSN login + PIN once | Plug in |

---

## 1. Setup (once)

Requires **Python 3.8** installed on Windows (`py -0` should list `3.8`), and
the PC on the **same LAN** as the console.

Double-click **`setup.bat`** (or run it in a terminal). It builds a virtual
environment and installs PySide6 + PyAV (~150 MB download).

Manual equivalent:

```bash
py -3.8 -m venv venv
venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 2. Enable Remote Play on the console (once)

On the PS5: **Settings → System → Remote Play → Enable Remote Play** (ON).
Make sure your PSN account is signed in on the console.

To allow waking it from standby later: **Settings → System → Power Saving →
Features Available in Rest Mode → "Stay Connected to the Internet"** and
**"Enable Turning On PS5 from Network"**.

## 3. Register this PC (once)

Double-click **`register.bat`**. It walks you through two steps:

1. **Link your PSN account.** It prints a Sony sign-in URL. Open it in your
   browser, log in, and when you land on a blank **"redirect"** page, copy that
   page's full URL and paste it back into the window.
   *Your password is only ever typed into your own browser — this app never
   sees it.*
2. **Link the console.** On the PS5 go to **Settings → System → Remote Play →
   Link Device**, which shows an **8-digit PIN**. Enter the console's IP and
   that PIN when prompted.

You only do this once per PC + console.

## 4. Capture

Double-click **`run.bat`**. In the window:

- Enter the **PS5 IP** (defaults to `192.168.20.38`), pick resolution / FPS /
  codec, and click **Connect**. If the console is in standby it will be woken
  automatically (give it ~30s).
- **● Record** starts an MP4; **■ Stop** finishes and saves it.
- **Screenshot** saves the current frame as a PNG.

Files are saved next to the app:

- `recordings/PS5_YYYYMMDD_HHMMSS.mp4`
- `screenshots/PS5_YYYYMMDD_HHMMSS.png`

## Play mode (play through the PC)

Remote Play fundamentally can't let you play on the console *locally* while it
captures — the moment you log in with a controller at the console, it ends the
Remote Play session. The way to **play and capture at the same time** is to play
*through* the app:

1. Connect a controller (DualSense / DualShock / Xbox) to the **PC** — USB or
   Bluetooth. (Pair it to the PC, not the console.)
2. Connect the app to the console as usual.
3. Tick **🎮 Play mode**. The label shows the detected controller; now your
   input drives the console and the stream stays rock-solid — recording and
   screenshots keep working.

Notes:
- The controller that's paired to the *console* staying off while you play this
  way is normal and expected (the console is being driven remotely).
- Sticks, d-pad, face buttons, L1/R1/L3/R3, Options/Create/PS, and L2/R2 (as
  on/off past half-press) are mapped. The touchpad *click* isn't mapped.
- Untick Play mode (or disconnect) to stop sending input.

---

## Settings

- **Resolution:** 540p / 720p / 1080p. **FPS:** 30 / 60.
- **Codec:** `h264` (compatible, default) or `hevc` (better quality, heavier to
  decode). The console does the encoding either way.
- **🔊 Audio** — play the console's audio live through your PC speakers (toggle
  any time).
- **Record audio** — mux the audio into recordings (AAC). Leave off for
  video-only files.
- **Auto-reconnect** — if the stream drops (see below), the app re-establishes
  it automatically; an in-progress recording keeps going with a short gap.
- **🎮 Play mode** — forward a controller plugged into the *PC* to the console,
  so you can play through the app (see below). Off by default; no input is sent
  to the console unless this is on.
- Recording re-encodes to H.264 (libx264) + AAC via PyAV — no external FFmpeg
  needed.
- Your last IP, resolution, FPS, codec and toggles are remembered in
  `config.json` next to the app.

## Limitations & notes

- **LAN only.** Same local network as the console (this build doesn't use the
  PSN internet relay).
- **One session at a time.** Remote Play allows a single active client. If the
  PS Remote Play app (or another client) is connected, this won't connect until
  that one closes. "Connecting" also takes the console out of whatever it's
  doing on-screen into the Remote Play session, same as normal Remote Play.
- **Capture only.** No controller, keyboard, or button input is ever
  transmitted — by design.
- **You can't play on the console locally while capturing.** When a controller
  logs in at the console, it ends the Remote Play session (black screen), and
  connecting a Remote Play session turns the console's local controller off.
  That's by design on every firmware, retail or jailbroken. To play *and*
  capture, use **Play mode** above (control through the PC). To play on the
  console itself and capture, you need a passive HDMI capture card instead.
- Auto-reconnect recovers from incidental drops, but it won't win a tug-of-war
  against someone logging in locally — that's the limitation above, not a bug.

## Troubleshooting

- **"Can't reach the console"** — wrong IP, different subnet, or the console is
  fully off / Remote Play not enabled.
- **"This PC isn't linked"** — run `register.bat` first.
- **"Session failed to start"** — another Remote Play client is already
  connected; close it and retry.
- **Black screen / no frames** — try `h264` instead of `hevc`, or a lower
  resolution; some HDR content streams poorly (a known `pyremoteplay` limit).
  If it followed waking a controller, that's the session drop described above —
  Auto-reconnect handles it.

### "Version not accepted" spam in the console log (is this normal?)

Yes — it's harmless. `pyremoteplay` hardcodes an older Remote Play protocol
version (`RP_VERSION_PS5 = "1.0"`, from 2022). Newer console firmware (e.g.
13.60) expects a newer version string, so the console logs **"Version not
accepted"** for the handshake/discovery probes — but it still negotiates down
and streams fine. The companion **"Network Test timed out → Default MTU and
RTT"** is the protocol's MTU-probing step (Senkusha) not completing; it just
falls back to default networking values. Neither stops capture from working.

## Project layout

```
PS5ScreenCapture/
  setup.bat / register.bat / run.bat
  requirements.txt
  config.json   # remembered IP + settings (created on first run)
  ps5capture/
    register.py   # PSN OAuth + PIN linking wizard (CLI)
    receiver.py   # AVReceiver subclass -> forwards decoded video + audio
    recorder.py   # PyAV MP4 writer: libx264 + AAC, real-time A/V timestamps
    audio.py      # live audio playback via sounddevice
    gamepad.py    # Play mode: PC controller (SDL) -> Remote Play input
    session.py    # RPDevice + asyncio lifecycle, reconnect, frame/audio fan-out
    config.py     # load/save config.json
    app.py        # PySide6 GUI: live view, audio, record, screenshot, auto-reconnect, play mode
```
