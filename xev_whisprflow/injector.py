"""
xev_whisprflow/injector.py

Types text at the current cursor position.

Auto-detects Wayland vs X11 at runtime:
  Wayland: wtype, fallback wl-copy + Ctrl+V
  X11:     xdotool, fallback xclip/xsel + Ctrl+V
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil

log = logging.getLogger(__name__)


def _is_wayland() -> bool:
    return bool(os.environ.get("WAYLAND_DISPLAY") or os.environ.get("XDG_SESSION_TYPE") == "wayland")


class TextInjector:
    def __init__(
        self,
        method: str = "auto",
        clipboard_fallback: bool = True,
        delay_ms: int = 50,
    ):
        self.clipboard_fallback = clipboard_fallback
        self.delay_ms = delay_ms
        self._wayland = _is_wayland()

        # Pick injection tool
        if method == "auto":
            if self._wayland:
                self._method = "wtype" if shutil.which("wtype") else "clipboard"
            else:
                self._method = "xdotool" if shutil.which("xdotool") else "clipboard"
        else:
            self._method = method

        # Clipboard tool
        if self._wayland:
            self._copy_cmd = "wl-copy" if shutil.which("wl-copy") else None
        else:
            if shutil.which("xclip"):
                self._copy_cmd = "xclip"
            elif shutil.which("xsel"):
                self._copy_cmd = "xsel"
            else:
                self._copy_cmd = None

        self._log_setup()

    def _log_setup(self):
        session = "Wayland" if self._wayland else "X11"
        log.info("Injector: %s session, method=%s, clipboard=%s", session, self._method, self._copy_cmd or "none")
        if self._method in ("wtype", "xdotool") and not shutil.which(self._method):
            tool = "wtype" if self._wayland else "xdotool"
            pkg = ("wtype" if self._wayland else "xdotool")
            log.warning("%s not found — install it: see README", tool)
        if not self._copy_cmd:
            pkg = "wl-clipboard" if self._wayland else "xclip"
            log.warning("No clipboard tool found — install %s for fallback injection", pkg)

    async def type_text(self, text: str) -> bool:
        if not text.strip():
            return True
        if self.delay_ms > 0:
            await asyncio.sleep(self.delay_ms / 1000)

        if self._method == "wtype":
            ok = await self._run("wtype", text)
            if ok:
                return True
            log.warning("wtype failed, trying clipboard fallback")

        elif self._method == "xdotool":
            ok = await self._xdotool(text)
            if ok:
                return True
            log.warning("xdotool failed, trying clipboard fallback")

        if self.clipboard_fallback and self._copy_cmd:
            return await self._clipboard_paste(text)

        log.error("All injection methods failed")
        return False

    async def _run(self, *cmd: str) -> bool:
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=5.0)
            if proc.returncode != 0:
                log.debug("%s exited %d: %s", cmd[0], proc.returncode, stderr.decode().strip())
                return False
            log.info("Injected %d chars via %s", len(cmd[-1]) if len(cmd) > 1 else 0, cmd[0])
            return True
        except asyncio.TimeoutError:
            log.error("%s timed out", cmd[0])
            return False
        except Exception as e:
            log.error("%s error: %s", cmd[0], e)
            return False

    async def _xdotool(self, text: str) -> bool:
        # xdotool type handles unicode and special chars better with --clearmodifiers
        try:
            proc = await asyncio.create_subprocess_exec(
                "xdotool", "type", "--clearmodifiers", "--", text,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=5.0)
            if proc.returncode != 0:
                log.debug("xdotool exited %d: %s", proc.returncode, stderr.decode().strip())
                return False
            log.info("Injected %d chars via xdotool", len(text))
            return True
        except asyncio.TimeoutError:
            log.error("xdotool timed out")
            return False
        except Exception as e:
            log.error("xdotool error: %s", e)
            return False

    async def _clipboard_paste(self, text: str) -> bool:
        try:
            # Copy to clipboard
            if self._wayland:
                copy_proc = await asyncio.create_subprocess_exec(
                    "wl-copy", text,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                )
            elif self._copy_cmd == "xclip":
                copy_proc = await asyncio.create_subprocess_exec(
                    "xclip", "-selection", "clipboard",
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                )
                await copy_proc.communicate(input=text.encode())
            else:  # xsel
                copy_proc = await asyncio.create_subprocess_exec(
                    "xsel", "--clipboard", "--input",
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                )
                await copy_proc.communicate(input=text.encode())

            if self._wayland:
                await asyncio.wait_for(copy_proc.communicate(), timeout=3.0)

            await asyncio.sleep(0.05)

            # Paste with Ctrl+V
            if self._wayland and shutil.which("wtype"):
                paste_ok = await self._run("wtype", "-M", "ctrl", "-k", "v", "-m", "ctrl")
            elif shutil.which("xdotool"):
                paste_ok = await self._run("xdotool", "key", "--clearmodifiers", "ctrl+v")
            else:
                log.error("No tool available to simulate Ctrl+V")
                return False

            if paste_ok:
                log.info("Injected %d chars via clipboard+Ctrl+V", len(text))
            return paste_ok

        except Exception as e:
            log.error("Clipboard paste error: %s", e)
            return False


if __name__ == "__main__":
    import sys
    import time

    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(message)s")
    text = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "Hello from xev-whisprflow!"
    print(f"Typing in 2s: {text!r}")
    print("Click into a text field NOW...")
    time.sleep(2)

    async def _test():
        injector = TextInjector()
        success = await injector.type_text(text)
        print(f"Success: {success}")

    asyncio.run(_test())
