"""
voiceflow/overlay.py

Full-screen transparent Wayland overlay with animated glowing border.

Backend selection (automatic, in priority order):
  1. GTK4 + gtk4-layer-shell  — true compositor overlay, above everything
  2. GTK4 plain window         — always-on-top hint, no layer-shell needed
  3. No overlay                — app still works fully, just no visual

The Fedora repos often ship gtk4-layer-shell built against GTK3, which
causes a namespace conflict. We detect this at import time and fall back
cleanly rather than crashing.

GTK must run on its own thread. All public methods are thread-safe.
"""
from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass
from enum import Enum, auto

log = logging.getLogger(__name__)


# ── Config ────────────────────────────────────────────────────────────────────

@dataclass
class OverlayConfig:
    color: str = "#7C3AED"
    width: int = 8
    fade_in_ms: int = 150
    fade_out_ms: int = 300
    glow_blur: int = 18
    min_alpha: float = 0.35
    max_alpha: float = 1.0


def _hex_to_rgb(hex_color: str) -> tuple[float, float, float]:
    h = hex_color.lstrip("#")
    return tuple(int(h[i : i + 2], 16) / 255.0 for i in (0, 2, 4))


# ── Backend detection ─────────────────────────────────────────────────────────

def _detect_backend() -> str:
    """
    Return 'layershell', 'gtk4', or 'none'.

    We must probe carefully: importing GtkLayerShell after Gtk 4.0 is
    already loaded will raise gi.RepositoryError if the installed
    GtkLayerShell typelib was built against GTK 3. We catch that and
    fall back to plain GTK4.
    """
    try:
        import gi
        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk  # noqa: F401 — just checking availability
    except Exception as e:
        log.warning("GTK4 not available: %s — overlay disabled", e)
        return "none"

    try:
        import gi
        gi.require_version("GtkLayerShell", "0.1")
        from gi.repository import GtkLayerShell  # noqa: F401
        log.info("Overlay backend: gtk4-layer-shell")
        return "layershell"
    except Exception as e:
        log.info(
            "gtk4-layer-shell not available (%s) — using plain GTK4 window.\n"
            "  The overlay will still appear but may not be above all windows.\n"
            "  To fix: sudo dnf install gtk4-layer-shell gtk4-layer-shell-devel\n"
            "  (Ensure the package is built against GTK4, not GTK3.)",
            e,
        )
        return "gtk4"


# ── Shared state (thread-safe via GIL + atomic float/enum) ───────────────────

class OverlayState(Enum):
    HIDDEN = auto()
    FADING_IN = auto()
    ACTIVE = auto()
    FADING_OUT = auto()
    ERROR_FLASH = auto()


class _SharedState:
    """Plain data container — written from any thread, read by GTK thread."""
    def __init__(self):
        self.amplitude: float = 0.0
        self.state: OverlayState = OverlayState.HIDDEN
        self.alpha: float = 0.0
        self.fade_start: float = 0.0
        self.error_flash_until: float = 0.0


# ── GTK window implementations ────────────────────────────────────────────────

class _BaseOverlayWindow:
    """Common drawing logic shared by both GTK backends."""

    def __init__(self, config: OverlayConfig, shared: _SharedState):
        self.config = config
        self.shared = shared
        self._rgb = _hex_to_rgb(config.color)
        self._error_rgb = _hex_to_rgb("#EF4444")
        self._app = None
        self._window = None
        self._drawing_area = None

    # ── Public controls (called from outside GTK thread) ──────────────────

    def show(self) -> None:
        self.shared.state = OverlayState.FADING_IN
        self.shared.fade_start = time.monotonic()

    def hide(self) -> None:
        self.shared.state = OverlayState.FADING_OUT
        self.shared.fade_start = time.monotonic()

    def flash_error(self) -> None:
        self.shared.error_flash_until = time.monotonic() + 0.6
        self.shared.state = OverlayState.ERROR_FLASH

    def set_amplitude(self, value: float) -> None:
        self.shared.amplitude = max(0.0, min(1.0, value))

    def quit(self) -> None:
        if self._app:
            try:
                from gi.repository import GLib
                GLib.idle_add(self._app.quit)
            except Exception:
                pass

    # ── Animation tick (runs inside GTK thread via GLib.timeout_add) ──────

    def _tick(self) -> bool:
        now = time.monotonic()
        s = self.shared

        if s.state == OverlayState.FADING_IN:
            progress = min((now - s.fade_start) / (self.config.fade_in_ms / 1000), 1.0)
            s.alpha = _ease(progress)
            if progress >= 1.0:
                s.state = OverlayState.ACTIVE

        elif s.state == OverlayState.FADING_OUT:
            progress = min((now - s.fade_start) / (self.config.fade_out_ms / 1000), 1.0)
            s.alpha = 1.0 - _ease(progress)
            if progress >= 1.0:
                s.state = OverlayState.HIDDEN
                s.alpha = 0.0

        elif s.state == OverlayState.ERROR_FLASH:
            if now >= s.error_flash_until:
                s.state = OverlayState.FADING_OUT
                s.fade_start = now
            else:
                phase = (now % 0.3) / 0.3
                s.alpha = 0.6 + 0.4 * math.sin(phase * math.pi)

        if self._drawing_area and s.state != OverlayState.HIDDEN:
            self._drawing_area.queue_draw()

        return True  # keep GLib timer alive

    # ── Cairo drawing (called by GTK on repaint) ──────────────────────────

    def _draw(self, area, cr, width: int, height: int) -> None:
        s = self.shared
        if s.state == OverlayState.HIDDEN or s.alpha < 0.01:
            return

        is_error = s.state == OverlayState.ERROR_FLASH
        r, g, b = self._error_rgb if is_error else self._rgb
        amp = s.amplitude if not is_error else 1.0

        glow_alpha = (
            self.config.min_alpha
            + (self.config.max_alpha - self.config.min_alpha) * amp
        ) * s.alpha

        border = self.config.width
        glow = self.config.glow_blur
        layers = 8

        for i in range(layers, 0, -1):
            spread = (i / layers) * glow
            layer_alpha = glow_alpha * (1 - i / (layers + 1)) * 0.7
            cr.set_source_rgba(r, g, b, layer_alpha)
            cr.rectangle(-spread, -spread, width + spread * 2, border + spread)
            cr.fill()
            cr.rectangle(-spread, height - border, width + spread * 2, border + spread)
            cr.fill()
            cr.rectangle(-spread, -spread, border + spread, height + spread * 2)
            cr.fill()
            cr.rectangle(width - border, -spread, border + spread, height + spread * 2)
            cr.fill()

        cr.set_source_rgba(r, g, b, glow_alpha)
        cr.set_line_width(border)
        cr.rectangle(border / 2, border / 2, width - border, height - border)
        cr.stroke()


class _LayerShellWindow(_BaseOverlayWindow):
    """GTK4 + gtk4-layer-shell backend — true compositor overlay."""

    def run(self) -> None:
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("GtkLayerShell", "0.1")
        from gi.repository import Gtk, GtkLayerShell, GLib, Gdk

        self._app = Gtk.Application(application_id="ai.voiceflow.overlay")
        self._app.connect("activate", self._activate)
        self._app.run(None)

    def _activate(self, app) -> None:
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("GtkLayerShell", "0.1")
        from gi.repository import Gtk, GtkLayerShell, GLib, Gdk

        win = Gtk.ApplicationWindow(application=app)
        win.set_title("voiceflow-overlay")
        win.set_decorated(False)
        self._window = win

        GtkLayerShell.init_for_window(win)
        GtkLayerShell.set_layer(win, GtkLayerShell.Layer.OVERLAY)
        for edge in (
            GtkLayerShell.Edge.TOP,
            GtkLayerShell.Edge.BOTTOM,
            GtkLayerShell.Edge.LEFT,
            GtkLayerShell.Edge.RIGHT,
        ):
            GtkLayerShell.set_anchor(win, edge, True)
        GtkLayerShell.set_exclusive_zone(win, -1)
        GtkLayerShell.set_keyboard_mode(win, GtkLayerShell.KeyboardMode.NONE)

        da = Gtk.DrawingArea()
        da.set_draw_func(self._draw)
        win.set_child(da)
        self._drawing_area = da

        css = Gtk.CssProvider()
        css.load_from_data(b"window { background: transparent; }")
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            css,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

        win.present()
        GLib.timeout_add(16, self._tick)
        log.info("Layer-shell overlay window active")


class _PlainGtk4Window(_BaseOverlayWindow):
    """
    GTK4 plain window fallback — no layer-shell.

    Uses set_decorated(False) + keep-above hint. Not a true compositor
    overlay, but works well enough for a visual indicator.
    On most compositors this will appear above regular windows.
    """

    def run(self) -> None:
        import gi
        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk, GLib

        self._app = Gtk.Application(application_id="ai.voiceflow.overlay.plain")
        self._app.connect("activate", self._activate)
        self._app.run(None)

    def _activate(self, app) -> None:
        import gi
        import cairo
        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk, Gdk, GLib

        display = Gdk.Display.get_default()
        monitor = display.get_monitors().get_item(0)
        geo = monitor.get_geometry()
        W, H = geo.width, geo.height

        win = Gtk.ApplicationWindow(application=app)
        win.set_title("voiceflow-overlay")
        win.set_decorated(False)
        win.set_default_size(W, H)
        win.set_resizable(False)
        self._window = win

        # Transparent background
        css = Gtk.CssProvider()
        css.load_from_data(b"window { background: transparent; }")
        Gtk.StyleContext.add_provider_for_display(
            display,
            css,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

        da = Gtk.DrawingArea()
        da.set_draw_func(self._draw)
        win.set_child(da)
        self._drawing_area = da

        win.present()

        # Make window click-through by setting an empty input region
        surface = win.get_surface()
        if surface:
            empty_region = cairo.Region()
            surface.set_input_region(empty_region)

        GLib.timeout_add(16, self._tick)
        log.info("Plain GTK4 overlay window active (no layer-shell)")


# ── Controller (public API) ───────────────────────────────────────────────────

class OverlayController:
    """
    High-level thread-safe controller.
    Detects the best available backend and starts it in a background thread.
    All methods are safe to call from asyncio or any thread.
    """

    def __init__(self, config: OverlayConfig | None = None):
        self._cfg = config or OverlayConfig()
        self._shared = _SharedState()
        self._window: _BaseOverlayWindow | None = None
        self._thread: threading.Thread | None = None
        self._backend = "none"

    def start(self) -> None:
        backend = _detect_backend()
        self._backend = backend

        if backend == "none":
            log.warning(
                "Overlay disabled — GTK4 not available.\n"
                "  Install: sudo dnf install gtk4 python3-gobject\n"
                "  VoiceFlow will still work fully without the visual overlay."
            )
            return

        if backend == "layershell":
            self._window = _LayerShellWindow(self._cfg, self._shared)
        else:
            self._window = _PlainGtk4Window(self._cfg, self._shared)

        self._thread = threading.Thread(
            target=self._window.run,
            name="gtk-overlay",
            daemon=True,
        )
        self._thread.start()
        log.info("Overlay controller started (backend=%s)", backend)

    def stop(self) -> None:
        if self._window:
            self._window.quit()

    def show(self) -> None:
        if self._window:
            self._window.show()

    def hide(self) -> None:
        if self._window:
            self._window.hide()

    def flash_error(self) -> None:
        if self._window:
            self._window.flash_error()

    def set_amplitude(self, value: float) -> None:
        if self._window:
            self._window.set_amplitude(value)

    @property
    def backend(self) -> str:
        return self._backend


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ease(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)
