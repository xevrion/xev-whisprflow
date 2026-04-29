# VoiceFlow

Push-to-talk voice dictation for Linux. Hold a key, speak, release — polished text appears wherever your cursor is.

Built for Wayland. Tested on Fedora with Hyprland.

```
hold Right Alt → speak → release → text injected
```

## How it works

1. **Hotkey** (evdev) detects the key press globally, no matter what's focused
2. **Mic** opens and starts recording (PipeWire via ALSA compat)
3. **Glowing border** appears around the screen while recording
4. On release, audio is sent to **Deepgram** (nova-3) for transcription
5. Raw transcript is cleaned up by **Groq** (llama-3.1-8b-instant)
6. Polished text is typed at your cursor via **wtype**

## Requirements

- Wayland compositor (Hyprland, Sway, GNOME, etc.)
- Fedora/RHEL (or adapt the install script)
- Python 3.11+
- [uv](https://github.com/astral-sh/uv)
- Free API keys: [Deepgram](https://deepgram.com) · [Groq](https://console.groq.com)

## Install

```bash
# System deps
sudo dnf install gtk4 python3-gobject wtype wl-clipboard pipewire pipewire-alsa portaudio-devel

# Add yourself to the input group (needed for global hotkey)
sudo usermod -aG input $USER
# Log out and back in after this

# Clone and set up
git clone <repo>
cd xev-whisprflow
uv venv --system-site-packages --python /usr/bin/python3
uv pip install -e .

# API keys
cp .env.example .env
# Edit .env and add your keys
```

## Hyprland

Add to `hyprland.conf` so the overlay doesn't steal focus or block clicks:

```ini
windowrulev2 = float, title:voiceflow-overlay
windowrulev2 = nofocus, title:voiceflow-overlay
windowrulev2 = noblur, title:voiceflow-overlay
windowrulev2 = pin, title:voiceflow-overlay
```

## Run

```bash
.venv/bin/python -m voiceflow.main
```

Or install as a systemd user service:

```bash
systemctl --user enable --now voiceflow
```

## Config

Config lives at `~/.config/voiceflow/config.toml` — created automatically on first run with all defaults documented inside.

Key options:

```toml
[hotkey]
key = "KEY_RIGHTALT"   # any evdev key name

[audio]
device = "Chu2 DSP Mono"   # pin to a specific mic; omit for system default
sample_rate = 48000

[overlay]
color = "#7C3AED"   # border glow color
```

Run `python -m voiceflow.hotkey --list` to see all valid key names.

## Picking a mic

```bash
.venv/bin/python -m voiceflow.audio --list
```

Set the device name in `config.toml` under `[audio]`. Match the sample rate to what your mic supports (usually 16000 or 48000).

## API keys

Both are free tiers with generous limits:

- `DEEPGRAM_API_KEY` — [deepgram.com](https://deepgram.com), free tier includes 200hrs/month
- `GROQ_API_KEY` — [console.groq.com](https://console.groq.com), free tier with high rate limits

Put them in `.env` in the project root (already gitignored).

## Troubleshooting

**Hotkey does nothing** — you're not in the `input` group yet, or the group change hasn't taken effect. Run `id | grep input`. If missing, run the usermod command above and fully log out/in (not just a new terminal).

**Wrong mic** — run the audio list command above and set `device` in config.

**Overlay covers screen on non-Hyprland** — add the equivalent window rules for your compositor, or ignore it (the overlay is cosmetic).

**gtk4-layer-shell warning at startup** — the overlay works without it, just won't be above all windows. Install a GTK4-compatible build of gtk4-layer-shell to fix.
