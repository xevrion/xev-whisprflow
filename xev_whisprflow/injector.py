"""
voiceflow/injector.py

Types text at the current cursor position using wtype (Wayland).

Falls back to clipboard paste (wl-copy + wtype Ctrl+V) for apps
that block synthetic keyboard events (e.g. some Electron apps).

Standalone test:
    python -m voiceflow.injector "Hello from VoiceFlow!"
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
import time

log = logging.getLogger(__name__)


class TextInjector:
    """
    Types text at the focused cursor position.
    Wayland: uses wtype (must be installed).
    Fallback: wl-copy + keyboard shortcut.
    """

    def __init__(
        self,
        method: str = "wtype",
        clipboard_fallback: bool = True,
        delay_ms: int = 50,
    ):
        self.method = method
        self.clipboard_fallback = clipboard_fallback
        self.delay_ms = delay_ms
        self._wtype_available = shutil.which("wtype") is not None
        self._wl_copy_available = shutil.which("wl-copy") is not None

        if not self._wtype_available:
            log.warning("wtype not found. Install: sudo dnf install wtype")
        if not self._wl_copy_available:
            log.warning("wl-copy not found. Install: sudo dnf install wl-clipboard")

    async def type_text(self, text: str) -> bool:
        """
        Type text at the current cursor position.
        Returns True on success, False on failure.
        """
        if not text.strip():
            return True

        # Small delay to let any overlay fade animation start
        if self.delay_ms > 0:
            await asyncio.sleep(self.delay_ms / 1000)

        if self._wtype_available:
            success = await self._wtype(text)
            if success:
                return True
            log.warning("wtype failed, trying clipboard fallback")

        if self.clipboard_fallback and self._wl_copy_available:
            return await self._clipboard_paste(text)

        log.error("All injection methods failed. Could not type: %r", text[:50])
        return False

    async def _wtype(self, text: str) -> bool:
        """Type text using wtype."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "wtype", text,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=5.0)
            if proc.returncode != 0:
                log.debug("wtype exited %d: %s", proc.returncode, stderr.decode().strip())
                return False
            log.info("Injected %d chars via wtype", len(text))
            return True
        except asyncio.TimeoutError:
            log.error("wtype timed out")
            return False
        except Exception as e:
            log.error("wtype error: %s", e)
            return False

    async def _clipboard_paste(self, text: str) -> bool:
        """
        Copy text to Wayland clipboard with wl-copy, then simulate Ctrl+V.
        Works in most apps that block wtype (Electron, some web browsers).
        """
        try:
            # Write to clipboard
            proc = await asyncio.create_subprocess_exec(
                "wl-copy", text,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=3.0)

            if proc.returncode != 0:
                log.error("wl-copy failed")
                return False

            # Brief pause to ensure clipboard is ready
            await asyncio.sleep(0.05)

            # Simulate Ctrl+V — press ctrl, press v, release v, release ctrl
            proc2 = await asyncio.create_subprocess_exec(
                "wtype", "-M", "ctrl", "-k", "v", "-m", "ctrl",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc2.communicate(), timeout=3.0)

            log.info("Injected %d chars via clipboard+Ctrl+V", len(text))
            return True

        except Exception as e:
            log.error("Clipboard paste error: %s", e)
            return False

    def check_dependencies(self) -> dict[str, bool]:
        """Return dict of dependency availability."""
        return {
            "wtype": self._wtype_available,
            "wl-copy": self._wl_copy_available,
        }


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    import time

    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(message)s")

    text = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "Hello from VoiceFlow! 🎤"

    print(f"Will type in 2 seconds: {text!r}")
    print("Click into a text field NOW...")
    time.sleep(2)

    async def _test():
        injector = TextInjector()
        deps = injector.check_dependencies()
        print(f"Dependencies: {deps}")
        success = await injector.type_text(text)
        print(f"Success: {success}")

    asyncio.run(_test())
