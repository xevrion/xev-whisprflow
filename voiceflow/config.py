"""
voiceflow/config.py

Loads configuration from:
  1. Hardcoded defaults (below)
  2. ~/.config/voiceflow/config.toml  (user overrides)
  3. Environment variables / .env file (API keys + quick overrides)

Access anywhere via:  from voiceflow.config import cfg
"""
from __future__ import annotations

import os
import logging
from pathlib import Path
from dataclasses import dataclass, field

import toml
from dotenv import load_dotenv

log = logging.getLogger(__name__)

# Load .env from cwd or project root — does nothing if file doesn't exist
load_dotenv()


@dataclass
class AudioConfig:
    sample_rate: int = 16000
    channels: int = 1
    dtype: str = "int16"
    chunk_ms: int = 100          # chunk size in milliseconds
    device: str | None = None    # None = system default mic


@dataclass
class HotkeyConfig:
    key: str = "KEY_RIGHTALT"    # evdev key name — see python-evdev docs
    # Alternative good choices: KEY_COMPOSE, KEY_RIGHTMETA, KEY_F13


@dataclass
class OverlayConfig:
    color: str = "#7C3AED"       # purple by default
    width: int = 8               # border thickness in pixels
    fade_in_ms: int = 150
    fade_out_ms: int = 300
    glow_blur: int = 18          # glow spread in pixels
    min_alpha: float = 0.35      # minimum glow when silent
    max_alpha: float = 1.0       # maximum glow at loud audio


@dataclass
class STTConfig:
    backend: str = "deepgram"    # "deepgram" | "local" (local = future)
    # Deepgram options
    model: str = "nova-3"
    language: str = "en"
    endpointing_ms: int = 300    # ms of silence to consider utterance done
    interim_results: bool = True
    # Local Whisper options (future)
    whisper_model: str = "medium"
    whisper_device: str = "cuda"


@dataclass
class LLMConfig:
    backend: str = "groq"        # "groq" | "local" (local = future)
    # Groq options
    model: str = "llama-3.1-8b-instant"
    max_tokens: int = 1024
    temperature: float = 0.1
    # Local options (future)
    local_model: str = "qwen2.5-0.5b"


@dataclass
class InjectorConfig:
    method: str = "wtype"        # "wtype" | "xdotool" (auto-detected)
    clipboard_fallback: bool = True
    delay_ms: int = 50           # small delay before typing (let focus settle)


@dataclass
class Config:
    audio: AudioConfig = field(default_factory=AudioConfig)
    hotkey: HotkeyConfig = field(default_factory=HotkeyConfig)
    overlay: OverlayConfig = field(default_factory=OverlayConfig)
    stt: STTConfig = field(default_factory=STTConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    injector: InjectorConfig = field(default_factory=InjectorConfig)

    # API keys — always from environment, never from config file
    deepgram_api_key: str = ""
    groq_api_key: str = ""

    # Runtime paths
    config_dir: Path = field(default_factory=lambda: Path.home() / ".config" / "voiceflow")
    data_dir: Path = field(default_factory=lambda: Path.home() / ".local" / "share" / "voiceflow")
    log_level: str = "INFO"


def _merge_toml(cfg: Config, data: dict) -> None:
    """Recursively merge TOML dict into Config dataclass."""
    for section, values in data.items():
        if not isinstance(values, dict):
            # Top-level scalar
            if hasattr(cfg, section):
                setattr(cfg, section, values)
            continue
        sub = getattr(cfg, section, None)
        if sub is None:
            continue
        for key, val in values.items():
            if hasattr(sub, key):
                setattr(sub, key, val)
            else:
                log.warning("Unknown config key: [%s] %s", section, key)


def load_config() -> Config:
    cfg = Config()

    # Load TOML user config if it exists
    toml_path = cfg.config_dir / "config.toml"
    if toml_path.exists():
        try:
            data = toml.load(toml_path)
            _merge_toml(cfg, data)
            log.info("Loaded config from %s", toml_path)
        except Exception as e:
            log.error("Failed to parse config.toml: %s — using defaults", e)

    # API keys always come from environment
    cfg.deepgram_api_key = os.environ.get("DEEPGRAM_API_KEY", "")
    cfg.groq_api_key = os.environ.get("GROQ_API_KEY", "")

    # Quick env overrides
    if v := os.environ.get("VOICEFLOW_HOTKEY"):
        cfg.hotkey.key = v
    if v := os.environ.get("VOICEFLOW_LANGUAGE"):
        cfg.stt.language = v
    if v := os.environ.get("VOICEFLOW_OVERLAY_COLOR"):
        cfg.overlay.color = v
    if v := os.environ.get("VOICEFLOW_LOG_LEVEL"):
        cfg.log_level = v

    # Validate
    if not cfg.deepgram_api_key:
        log.warning("DEEPGRAM_API_KEY is not set — STT will fail")
    if not cfg.groq_api_key:
        log.warning("GROQ_API_KEY is not set — LLM cleanup disabled, raw transcript will be injected")

    # Ensure dirs exist
    cfg.config_dir.mkdir(parents=True, exist_ok=True)
    cfg.data_dir.mkdir(parents=True, exist_ok=True)

    return cfg


# Global singleton — import this everywhere
cfg: Config = load_config()


def write_default_config() -> None:
    """Write a fully documented default config.toml to the user config dir."""
    toml_path = cfg.config_dir / "config.toml"
    if toml_path.exists():
        return  # Never overwrite existing config

    content = """\
# VoiceFlow configuration
# Edit this file to customize your setup.
# All values shown are the defaults.

[hotkey]
# evdev key name for the push-to-talk hotkey.
# Run `python -m voiceflow.hotkey --list` to see available keys.
key = "KEY_RIGHTALT"

[overlay]
color = "#7C3AED"        # Border glow color (hex)
width = 8                # Border thickness in pixels
fade_in_ms = 150
fade_out_ms = 300
glow_blur = 18
min_alpha = 0.35         # Glow intensity when silent
max_alpha = 1.0          # Glow intensity at loud audio

[audio]
sample_rate = 16000
channels = 1
chunk_ms = 100           # Audio chunk size in milliseconds
# device = "default"     # Uncomment to pin to a specific device

[stt]
backend = "deepgram"     # "deepgram" or "local" (local = future feature)
model = "nova-3"
language = "en"
endpointing_ms = 300

[llm]
backend = "groq"         # "groq" or "local" (local = future feature)
model = "llama-3.1-8b-instant"
temperature = 0.1

[injector]
method = "wtype"         # "wtype" for Wayland, "xdotool" for X11
clipboard_fallback = true
delay_ms = 50
"""
    toml_path.write_text(content)
    log.info("Wrote default config to %s", toml_path)
