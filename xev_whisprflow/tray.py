"""
voiceflow/tray.py

System tray icon for VoiceFlow.

Try order:
  1. AppIndicator3 (libappindicator — best on GNOME/KDE)
  2. pystray (portable fallback)
  3. Nothing — log a warning and continue

Run in a daemon thread via TrayIcon.start().
Call tray.set_state("recording"|"processing"|"idle") from any thread.
"""
from __future__ import annotations

import logging
import subprocess
import threading
from typing import Callable

log = logging.getLogger(__name__)

DASHBOARD_URL = "http://localhost:7878"


class TrayIcon:
    def __init__(self, on_quit: Callable[[], None] | None = None):
        self._on_quit = on_quit or (lambda: None)
        self._state = "idle"
        self._impl: _TrayImpl | None = None

    def start(self) -> None:
        impl = _try_appindicator(self._on_quit)
        if impl is None:
            impl = _try_pystray(self._on_quit)
        if impl is None:
            log.warning("No tray icon backend available (tried AppIndicator3 and pystray). Tray disabled.")
            return
        self._impl = impl
        t = threading.Thread(target=self._impl.run, name="tray", daemon=True)
        t.start()
        log.info("Tray icon started (%s)", type(self._impl).__name__)

    def stop(self) -> None:
        if self._impl:
            self._impl.stop()

    def set_state(self, state: str) -> None:
        """Update tray tooltip/icon to reflect current app state."""
        self._state = state
        if self._impl:
            self._impl.set_state(state)


# ---------------------------------------------------------------------------
# Internal implementations
# ---------------------------------------------------------------------------

class _TrayImpl:
    def run(self) -> None: ...
    def stop(self) -> None: ...
    def set_state(self, state: str) -> None: ...


def _open_dashboard() -> None:
    try:
        subprocess.Popen(["xdg-open", DASHBOARD_URL],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        log.warning("xdg-open failed: %s", e)


# ---- AppIndicator3 ----

class _AppIndicatorImpl(_TrayImpl):
    def __init__(self, on_quit: Callable[[], None]):
        self._on_quit = on_quit
        self._indicator = None
        self._loop = None  # GLib main loop

    def run(self) -> None:
        import gi
        gi.require_version("Gtk", "3.0")
        gi.require_version("AppIndicator3", "0.1")
        from gi.repository import Gtk, AppIndicator3, GLib  # type: ignore

        self._indicator = AppIndicator3.Indicator.new(
            "voiceflow",
            "audio-input-microphone",
            AppIndicator3.IndicatorCategory.APPLICATION_STATUS,
        )
        self._indicator.set_status(AppIndicator3.IndicatorStatus.ACTIVE)
        self._indicator.set_title("VoiceFlow")

        menu = Gtk.Menu()

        item_dash = Gtk.MenuItem(label="Open Dashboard")
        item_dash.connect("activate", lambda _: _open_dashboard())
        menu.append(item_dash)

        menu.append(Gtk.SeparatorMenuItem())

        item_quit = Gtk.MenuItem(label="Quit VoiceFlow")
        item_quit.connect("activate", lambda _: self._quit())
        menu.append(item_quit)

        menu.show_all()
        self._indicator.set_menu(menu)

        self._loop = GLib.MainLoop()
        self._loop.run()

    def stop(self) -> None:
        if self._loop:
            self._loop.quit()

    def set_state(self, state: str) -> None:
        # Could swap icons per state; stock icon is sufficient for v1
        pass

    def _quit(self) -> None:
        self.stop()
        self._on_quit()


def _try_appindicator(on_quit: Callable[[], None]) -> _TrayImpl | None:
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        gi.require_version("AppIndicator3", "0.1")
        from gi.repository import AppIndicator3  # noqa: F401
        return _AppIndicatorImpl(on_quit)
    except Exception:
        return None


# ---- pystray fallback ----

class _PystrayImpl(_TrayImpl):
    def __init__(self, on_quit: Callable[[], None]):
        self._on_quit = on_quit
        self._icon = None

    def run(self) -> None:
        try:
            import pystray
            from PIL import Image, ImageDraw
        except ImportError:
            log.warning("pystray or Pillow not installed — tray disabled")
            return

        # Create a tiny purple circle icon (16x16)
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.ellipse([8, 8, 56, 56], fill=(124, 58, 237, 255))

        menu = pystray.Menu(
            pystray.MenuItem("Open Dashboard", lambda icon, item: _open_dashboard()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit VoiceFlow", lambda icon, item: self._quit(icon)),
        )

        self._icon = pystray.Icon("voiceflow", img, "VoiceFlow", menu)
        self._icon.run()

    def stop(self) -> None:
        if self._icon:
            try:
                self._icon.stop()
            except Exception:
                pass

    def set_state(self, state: str) -> None:
        pass  # Could update icon color; skipped for v1

    def _quit(self, icon) -> None:
        icon.stop()
        self._on_quit()


def _try_pystray(on_quit: Callable[[], None]) -> _TrayImpl | None:
    try:
        import pystray  # noqa: F401
        from PIL import Image  # noqa: F401
        return _PystrayImpl(on_quit)
    except (ImportError, ValueError):
        return None
