"""
xev-whisprflow/main.py

The async orchestrator — wires all components together.

State machine:
    IDLE → (hotkey press) → RECORDING → (hotkey release) →
    PROCESSING → (inject done) → IDLE

Everything runs in one asyncio event loop.
GTK overlay runs in a separate thread (GTK must own its thread).
Hotkey listener runs in a separate thread (evdev is blocking).
Dashboard runs in a background thread (uvicorn).
Tray icon runs in a daemon thread.
"""
from __future__ import annotations

import asyncio
import datetime
import logging
import signal
import sys
from enum import Enum, auto
from pathlib import Path

log = logging.getLogger(__name__)


class AppState(Enum):
    IDLE = auto()
    RECORDING = auto()
    PROCESSING = auto()


class App:
    """Main application — ties together all subsystems."""

    def __init__(self):
        from xev_whisprflow.config import cfg, write_default_config
        write_default_config()
        self.cfg = cfg

        self._setup_logging()

        from xev_whisprflow.hotkey import HotkeyListener, HotkeyEvent
        from xev_whisprflow.audio import AudioCapture
        from xev_whisprflow.overlay import OverlayController, OverlayConfig
        from xev_whisprflow.injector import TextInjector

        self._HotkeyEvent = HotkeyEvent
        self._state = AppState.IDLE
        self._loop: asyncio.AbstractEventLoop | None = None

        # Subsystems
        self._audio = AudioCapture(
            sample_rate=cfg.audio.sample_rate,
            channels=cfg.audio.channels,
            chunk_ms=cfg.audio.chunk_ms,
            device=cfg.audio.device,
        )

        self._overlay = OverlayController(
            OverlayConfig(
                color=cfg.overlay.color,
                width=cfg.overlay.width,
                fade_in_ms=cfg.overlay.fade_in_ms,
                fade_out_ms=cfg.overlay.fade_out_ms,
                glow_blur=cfg.overlay.glow_blur,
                min_alpha=cfg.overlay.min_alpha,
                max_alpha=cfg.overlay.max_alpha,
            )
        )

        self._injector = TextInjector(
            method=cfg.injector.method,
            clipboard_fallback=cfg.injector.clipboard_fallback,
            delay_ms=cfg.injector.delay_ms,
        )

        self._hotkey: "HotkeyListener | None" = None
        self._amplitude_task: asyncio.Task | None = None

        # Dashboard & tray
        from xev_whisprflow.dashboard import DashboardServer
        from xev_whisprflow.tray import TrayIcon

        dashboard_port = 7878
        try:
            dashboard_port = cfg.dashboard.port  # type: ignore[attr-defined]
        except AttributeError:
            pass

        self._dashboard = DashboardServer(port=dashboard_port)
        self._tray = TrayIcon(on_quit=self._shutdown)

    def _setup_logging(self) -> None:
        level = getattr(logging, self.cfg.log_level.upper(), logging.INFO)
        logging.basicConfig(
            level=level,
            format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
            datefmt="%H:%M:%S",
            stream=sys.stderr,
        )
        # Quiet noisy third-party loggers
        for noisy in ("httpx", "httpcore", "websockets", "urllib3", "uvicorn"):
            logging.getLogger(noisy).setLevel(logging.WARNING)

    async def run(self) -> None:
        self._loop = asyncio.get_running_loop()

        # Print startup banner
        self._print_banner()

        # Check dependencies
        self._check_deps()

        # Start overlay (GTK in background thread)
        self._overlay.start()

        # Start dashboard server
        self._dashboard.start()

        # Start tray icon
        self._tray.start()

        # Start hotkey listener
        from xev_whisprflow.hotkey import HotkeyListener
        self._hotkey = HotkeyListener(self.cfg.hotkey.key, self._loop)
        self._hotkey.start()

        # Handle Ctrl+C / SIGTERM gracefully
        for sig in (signal.SIGINT, signal.SIGTERM):
            self._loop.add_signal_handler(sig, self._shutdown)

        log.info(
            "xev-whisprflow ready. Hold %s to dictate. Press Ctrl+C to quit.",
            self.cfg.hotkey.key,
        )

        # Broadcast initial idle status
        await self._broadcast_status()

        try:
            await self._event_loop()
        except asyncio.CancelledError:
            pass
        finally:
            await self._cleanup()
            import os; os._exit(0)

    async def _event_loop(self) -> None:
        """Main event loop — process hotkey events."""
        while True:
            event = await self._hotkey.queue.get()

            if event == self._HotkeyEvent.PRESSED:
                if self._state == AppState.IDLE:
                    await self._on_press()

            elif event == self._HotkeyEvent.RELEASED:
                if self._state == AppState.RECORDING:
                    await self._on_release()

    async def _on_press(self) -> None:
        """Hotkey pressed — start recording."""
        log.debug("Hotkey pressed → start recording")
        self._state = AppState.RECORDING
        self._tray.set_state("recording")

        self._overlay.show()
        self._audio.start_recording(self._loop)

        # Broadcast recording state
        await self._broadcast_status()

        # Start amplitude update task for overlay animation
        self._amplitude_task = asyncio.create_task(self._update_amplitude())

    async def _on_release(self) -> None:
        """Hotkey released — stop recording, process, inject."""
        log.debug("Hotkey released → processing")
        self._state = AppState.PROCESSING
        self._tray.set_state("processing")

        # Stop audio capture
        self._audio.stop_recording()

        # Cancel amplitude task
        if self._amplitude_task:
            self._amplitude_task.cancel()
            self._amplitude_task = None
        self._overlay.set_amplitude(0.0)

        # Broadcast processing state
        await self._broadcast_status()
        self._dashboard.broadcast({"type": "amplitude", "value": 0.0})

        # Collect all audio
        audio_chunks = []
        async for chunk in self._audio.audio_chunks():
            audio_chunks.append(chunk)

        if not audio_chunks:
            log.warning("No audio captured — ignoring")
            self._overlay.hide()
            self._state = AppState.IDLE
            self._tray.set_state("idle")
            await self._broadcast_status()
            return

        import numpy as np
        audio_bytes = np.concatenate(audio_chunks).tobytes()
        duration = len(audio_bytes) / (self.cfg.audio.sample_rate * 2)
        log.info("Captured %.2fs of audio (%d bytes)", duration, len(audio_bytes))

        if duration < 0.3:
            log.warning("Audio too short (<0.3s) — ignoring")
            self._overlay.hide()
            self._state = AppState.IDLE
            self._tray.set_state("idle")
            await self._broadcast_status()
            return

        transcript: str = ""
        polished: str = ""

        # Run STT + LLM concurrently-ish (STT must finish before LLM)
        try:
            transcript = await self._transcribe(audio_bytes)
            if transcript:
                polished = await self._polish(transcript)
                await self._inject(polished)
                # Broadcast transcript event
                self._dashboard.broadcast({
                    "type": "transcript",
                    "raw": transcript,
                    "polished": polished,
                    "ts": datetime.datetime.now().isoformat(),
                })
            else:
                log.warning("Empty transcript — nothing to inject")
                self._overlay.flash_error()
                self._dashboard.broadcast({"type": "error", "message": "Empty transcript"})
        except Exception as e:
            log.error("Processing pipeline error: %s", e, exc_info=True)
            self._overlay.flash_error()
            self._dashboard.broadcast({"type": "error", "message": str(e)})
        finally:
            self._overlay.hide()
            self._state = AppState.IDLE
            self._tray.set_state("idle")
            await self._broadcast_status()
            # Save to history
            if transcript:
                self._save_history(transcript, polished if polished else transcript)

    async def _transcribe(self, audio_bytes: bytes) -> str:
        """Run STT on audio bytes, return transcript string."""
        from xev_whisprflow.stt import DeepgramSTT

        log.info("Sending audio to Deepgram...")

        stt = DeepgramSTT(
            api_key=self.cfg.deepgram_api_key,
            model=self.cfg.stt.model,
            language=self.cfg.stt.language,
            sample_rate=self.cfg.audio.sample_rate,
            channels=self.cfg.audio.channels,
            endpointing_ms=self.cfg.stt.endpointing_ms,
        )

        async with stt:
            # Send audio in chunks
            chunk_size = self.cfg.audio.sample_rate * 2 // 10  # 100ms
            for i in range(0, len(audio_bytes), chunk_size):
                await stt.send_audio(audio_bytes[i : i + chunk_size])
                await asyncio.sleep(0.005)
            return await stt.finalize()

    async def _polish(self, raw_text: str) -> str:
        """Run LLM cleanup on raw transcript."""
        from xev_whisprflow.llm import polish_transcript

        if not self.cfg.groq_api_key:
            return raw_text

        log.info("Polishing transcript with Groq...")
        return await polish_transcript(
            raw_text,
            api_key=self.cfg.groq_api_key,
            model=self.cfg.llm.model,
            temperature=self.cfg.llm.temperature,
            max_tokens=self.cfg.llm.max_tokens,
        )

    async def _inject(self, text: str) -> None:
        """Type text at cursor."""
        log.info("Injecting: %r", text[:80])
        await self._injector.type_text(text)

    async def _update_amplitude(self) -> None:
        """Continuously poll audio amplitude and update overlay + dashboard."""
        while self._state == AppState.RECORDING:
            amp = self._audio.amplitude
            self._overlay.set_amplitude(amp)
            self._dashboard.broadcast({"type": "amplitude", "value": round(amp, 4)})
            await asyncio.sleep(0.033)  # ~30fps update

    async def _broadcast_status(self) -> None:
        """Push current state to all dashboard WebSocket clients."""
        import time
        state_name = self._state.name.lower()
        self._dashboard.broadcast({
            "type": "status",
            "state": state_name,
            "uptime_s": 0,  # dashboard computes its own uptime
        })

    def _save_history(self, raw: str, polished: str) -> None:
        """Append session to local history file."""
        history_file = self.cfg.data_dir / "history.jsonl"
        try:
            import json
            entry = {
                "ts": datetime.datetime.now().isoformat(),
                "raw": raw,
                "polished": polished,
            }
            with open(history_file, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            log.debug("Failed to save history: %s", e)

    def _check_deps(self) -> None:
        import shutil, os
        wayland = bool(os.environ.get("WAYLAND_DISPLAY") or os.environ.get("XDG_SESSION_TYPE") == "wayland")
        if wayland:
            if not shutil.which("wtype"):
                log.warning("wtype not found (Wayland injection). Install: wtype")
            if not shutil.which("wl-copy"):
                log.warning("wl-copy not found (clipboard fallback). Install: wl-clipboard")
        else:
            if not shutil.which("xdotool"):
                log.warning("xdotool not found (X11 injection). Install: xdotool")
            if not shutil.which("xclip") and not shutil.which("xsel"):
                log.warning("xclip/xsel not found (clipboard fallback). Install: xclip")

    def _shutdown(self) -> None:
        log.info("Shutting down...")
        self._dashboard.stop()
        self._overlay.stop()
        self._tray.stop()
        if self._loop:
            for task in asyncio.all_tasks(self._loop):
                task.cancel()

    async def _cleanup(self) -> None:
        if self._hotkey:
            self._hotkey.stop()
        if self._audio.is_recording:
            self._audio.stop_recording()
        self._overlay.stop()
        self._dashboard.stop()
        self._tray.stop()
        log.info("xev-whisprflow stopped.")

    def _print_banner(self) -> None:
        print(
            "\n  xev-whisprflow\n"
            f"  hotkey    {self.cfg.hotkey.key}\n"
            f"  stt       Deepgram {self.cfg.stt.model} ({self.cfg.stt.language})\n"
            f"  llm       Groq {self.cfg.llm.model}\n"
            f"  inject    {self.cfg.injector.method}\n"
            f"  config    {self.cfg.config_dir}/config.toml\n"
            f"  dashboard http://localhost:7878\n"
        )


def cli_entry() -> None:
    """Entry point for `xev-whisprflow` CLI command."""
    app = App()
    asyncio.run(app.run())


if __name__ == "__main__":
    cli_entry()
