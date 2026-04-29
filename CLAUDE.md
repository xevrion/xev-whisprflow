# VoiceFlow — Claude Code Instructions

## What this project is
A native Linux voice dictation daemon — a Wispr Flow clone for Linux (Wayland-first).
Press a hotkey anywhere → glowing screen border appears → speak → release → polished text is typed wherever your cursor is.

## Architecture (read this before touching anything)
```
hotkey (evdev) → overlay (GTK4 layer-shell) + audio (PipeWire/sounddevice)
     → STT (Deepgram WebSocket) → LLM polish (Groq) → text inject (wtype)
```

All running as a single Python process with async concurrency (`asyncio`).
The hotkey listener runs in a thread (evdev is blocking), everything else is async.

## Project structure
```
voiceflow/
├── voiceflow/
│   ├── __init__.py
│   ├── main.py          ← asyncio entry point, wires everything together
│   ├── hotkey.py        ← evdev global hotkey listener (thread → async queue)
│   ├── overlay.py       ← GTK4 + gtk4-layer-shell glowing border window
│   ├── audio.py         ← PipeWire/ALSA mic capture via sounddevice
│   ├── stt.py           ← Deepgram WebSocket streaming client
│   ├── llm.py           ← Groq API cleanup call
│   ├── injector.py      ← wtype (Wayland) text injection
│   └── config.py        ← TOML config loader with defaults
├── systemd/
│   └── voiceflow.service  ← systemd --user unit file
├── install.sh             ← one-command Fedora installer
├── pyproject.toml
├── .env.example
└── CLAUDE.md              ← this file
```

## Key decisions already made — do not change without reason
- **Wayland only** for v1 (wtype for injection, gtk4-layer-shell for overlay)
- **Deepgram nova-3** for STT v1 (WebSocket streaming, not REST)
- **Groq llama-3.1-8b-instant** for LLM cleanup v1
- **Python 3.11+** with asyncio throughout
- **evdev** for hotkey (needs user in `input` group — installer handles this)
- **sounddevice** wraps PipeWire via ALSA compat layer (no extra PW bindings needed)
- Config file at `~/.config/voiceflow/config.toml`
- API keys from `.env` file or environment variables

## System dependencies (Fedora/RHEL)
```
dnf install python3-pip python3-devel gtk4-devel libadwaita-devel
dnf install wtype pipewire pipewire-alsa portaudio-devel
dnf install python3-gobject gtk4 gtk4-layer-shell
```
Also needs: `pip install python-evdev sounddevice deepgram-sdk groq toml python-dotenv pygobject`

## Running locally during development
```bash
cp .env.example .env
# fill in your API keys
python -m voiceflow.main
```

## Testing individual components
```bash
python -m voiceflow.hotkey     # prints events, no side effects
python -m voiceflow.audio      # records 3s, saves test.wav
python -m voiceflow.stt        # transcribes test.wav via Deepgram
python -m voiceflow.injector   # types "VoiceFlow test" at cursor
```

## Default hotkey
`Right Alt` held = recording. Release = process + inject.
Configurable in `~/.config/voiceflow/config.toml`.

## Overlay behaviour
- Full-screen transparent window, layer-shell overlay type, above everything
- 8px glowing border, color = `#7C3AED` (purple) by default
- Border brightness scales with mic amplitude (quiet=dim, loud=bright)
- On hotkey press: fade in over 150ms
- On hotkey release: hold until text is injected, then fade out over 300ms
- If STT/LLM fails: border flashes red once, then fades

## Audio format
- Sample rate: 16000 Hz
- Channels: 1 (mono)
- Format: int16
- Chunk size: 1600 samples = 100ms per chunk

## Deepgram config
- Model: nova-3
- Encoding: linear16
- Sample rate: 16000
- Language: en (configurable)
- Interim results: true (for live overlay preview)
- Endpointing: 300ms silence

## Groq prompt (do not change the system prompt wording)
System: "You are a transcription polisher. Fix grammar, punctuation, remove filler words (um, uh, like), and fix run-on sentences. Preserve meaning exactly. Return ONLY the cleaned text, nothing else."

## Error handling rules
1. If Deepgram WebSocket fails to connect → show red overlay flash, log error, do NOT crash
2. If Groq fails → inject the raw Deepgram transcript (better than nothing)
3. If wtype fails → fall back to xdg clipboard paste (wl-copy + wtype ctrl+v simulation)
4. If no mic found → show red flash, log, exit gracefully

## What to build next (not in v1)
- Local Whisper.cpp backend (settings toggle: `stt_backend = "local"`)
- Local Qwen2.5-0.5B via llama.cpp (settings toggle: `llm_backend = "local"`)
- Live transcript preview in overlay (partial results from Deepgram)
- Tray icon via AppIndicator3
- History viewer GUI
- Custom voice commands ("hey flow, new paragraph")
