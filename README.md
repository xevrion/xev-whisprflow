# xev-whisprflow

Push-to-talk voice dictation for Linux. Hold a key, speak, release, and polished text appears wherever your cursor is.

Built for Wayland. Tested on Fedora with Hyprland.

```
hold Right Alt -> speak -> release -> text injected
```

## Install

One command:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/xevrion/xev-whisprflow/main/install.sh)
```

Or clone and run:

```bash
git clone https://github.com/xevrion/xev-whisprflow.git
cd xev-whisprflow
bash install.sh
```

The installer handles system packages, the Python venv, the CLI symlink, API key setup, and the systemd service.

You'll need free API keys from [Deepgram](https://deepgram.com) and [Groq](https://console.groq.com) — the installer will ask for them.

## Usage

```bash
xev-whisprflow          # run in foreground
```

Or as a background service that starts on login:

```bash
systemctl --user start xev-whisprflow
journalctl --user -u xev-whisprflow -f
```

Dashboard at `http://localhost:7878` while the app is running.

## How it works

1. **Hotkey** (evdev) detects the key globally, regardless of what's focused
2. **Mic** opens and starts recording via PipeWire
3. **Glowing border** appears around the screen while recording
4. On release, audio goes to **Deepgram** nova-3 for transcription
5. Raw transcript is cleaned up by **Groq** llama-3.1-8b-instant
6. Polished text is typed at your cursor via **wtype**

## Requirements

- Linux (Fedora, Ubuntu/Debian, Arch — Wayland or X11)
- Free API keys: [Deepgram](https://deepgram.com) and [Groq](https://console.groq.com)

## Config

Config lives at `~/.config/xev-whisprflow/config.toml`, created on first run.

```toml
[hotkey]
key = "KEY_RIGHTALT"

[audio]
device = "Chu2 DSP Mono"   # omit for system default
sample_rate = 48000

[overlay]
color = "#7C3AED"
```

Settings can also be changed from the dashboard at `http://localhost:7878`.

## Hyprland

Add to `hyprland.conf` so the overlay doesn't steal focus or block clicks:

```ini
windowrulev2 = float, title:voiceflow-overlay
windowrulev2 = nofocus, title:voiceflow-overlay
windowrulev2 = noblur, title:voiceflow-overlay
windowrulev2 = pin, title:voiceflow-overlay
```

## Troubleshooting

**Hotkey does nothing:** check `id | grep input`. If the `input` group is missing, run `sudo usermod -aG input $USER` and fully log out/in.

**Wrong mic:** open the dashboard settings or set `device` in config. Run `xev-whisprflow --list-devices` equivalent via `python -m xev_whisprflow.audio --list`.

**Overlay covers screen:** add the Hyprland window rules above, or the equivalent for your compositor.

**gtk4-layer-shell warning:** the overlay works without it but won't float above all windows. Install a GTK4-compatible build to fix.

**X11 users:** text injection uses `xdotool` automatically. Install it if not present (`xdotool` package on all distros). Clipboard fallback uses `xclip`.

**Ubuntu/Debian:** the installer uses `apt`. Arch: `pacman`. The installer auto-detects your distro.
