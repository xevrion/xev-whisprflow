"""
voiceflow/hotkey.py

Global hotkey listener using evdev (raw Linux input events).
Runs in a dedicated thread and puts events into an asyncio Queue.

The listener captures events at the /dev/input level, so it works
regardless of window manager or compositor.

IMPORTANT: The user must be in the 'input' group for evdev access.
The installer handles this:  sudo usermod -aG input $USER

Usage as standalone test:
    python -m voiceflow.hotkey
"""
from __future__ import annotations

import asyncio
import logging
import threading
from enum import Enum, auto
from pathlib import Path

log = logging.getLogger(__name__)


class HotkeyEvent(Enum):
    PRESSED = auto()
    RELEASED = auto()


class HotkeyListener:
    """
    Listens for a specific key on all /dev/input/event* devices.
    Thread-safe: runs evdev in a background thread, delivers events
    to an asyncio Queue for consumption in the main event loop.
    """

    def __init__(self, key_name: str, loop: asyncio.AbstractEventLoop):
        self.key_name = key_name
        self.loop = loop
        self.queue: asyncio.Queue[HotkeyEvent] = asyncio.Queue()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run,
            name="hotkey-listener",
            daemon=True,
        )
        self._thread.start()
        log.info("Hotkey listener started — watching for %s", self.key_name)

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def _run(self) -> None:
        try:
            import evdev
            from evdev import ecodes
        except ImportError:
            log.error("python-evdev not installed. Run: pip install evdev")
            return

        # Resolve the key code from name
        try:
            target_code = ecodes.ecodes[self.key_name]
        except KeyError:
            log.error("Unknown key name: %s. Run python -m voiceflow.hotkey --list", self.key_name)
            return

        # Find all keyboard-capable devices
        devices = self._find_keyboards(evdev)
        if not devices:
            log.error(
                "No input devices found. Are you in the 'input' group?\n"
                "  Run: sudo usermod -aG input $USER  (then log out/in)"
            )
            return

        log.debug("Monitoring %d input device(s)", len(devices))

        # Use select to monitor multiple devices simultaneously
        import select
        fd_map = {dev.fd: dev for dev in devices}

        while not self._stop_event.is_set():
            try:
                r, _, _ = select.select(fd_map.keys(), [], [], 0.1)
            except Exception as e:
                log.debug("select error: %s", e)
                continue

            for fd in r:
                dev = fd_map[fd]
                try:
                    for event in dev.read():
                        if event.type == evdev.ecodes.EV_KEY and event.code == target_code:
                            if event.value == evdev.KeyEvent.key_down:
                                self._emit(HotkeyEvent.PRESSED)
                            elif event.value == evdev.KeyEvent.key_up:
                                self._emit(HotkeyEvent.RELEASED)
                except OSError:
                    # Device disconnected
                    log.debug("Device %s disconnected", dev.path)
                    del fd_map[fd]
                    try:
                        dev.close()
                    except Exception:
                        pass

    def _emit(self, event: HotkeyEvent) -> None:
        """Thread-safe push to asyncio queue."""
        self.loop.call_soon_threadsafe(self.queue.put_nowait, event)

    @staticmethod
    def _find_keyboards(evdev) -> list:
        """Return all devices that have keyboard keys."""
        keyboards = []
        for path in evdev.list_devices():
            try:
                dev = evdev.InputDevice(path)
                caps = dev.capabilities()
                if evdev.ecodes.EV_KEY in caps:
                    keyboards.append(dev)
                    log.debug("Watching input device: %s (%s)", dev.name, path)
                else:
                    dev.close()
            except (PermissionError, OSError) as e:
                log.debug("Cannot open %s: %s", path, e)
        return keyboards


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(message)s")

    if "--list" in sys.argv:
        try:
            from evdev import ecodes
            keys = [k for k in dir(ecodes) if k.startswith("KEY_")]
            print("\n".join(sorted(keys)))
        except ImportError:
            print("evdev not installed")
        sys.exit(0)

    print(f"Listening for KEY_RIGHTALT events. Press Ctrl+C to stop.\n")

    async def _test():
        loop = asyncio.get_event_loop()
        listener = HotkeyListener("KEY_RIGHTALT", loop)
        listener.start()
        try:
            while True:
                event = await listener.queue.get()
                print(f"  → {event.name}")
        except KeyboardInterrupt:
            pass
        finally:
            listener.stop()

    asyncio.run(_test())
