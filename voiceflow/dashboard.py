"""
voiceflow/dashboard.py

FastAPI dashboard server — serves the web UI and pushes real-time events
over WebSocket to all connected clients.

Run as a background thread (uvicorn) so it doesn't block the asyncio loop.
The main pipeline calls `await dashboard.broadcast(event_dict)` to push events.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
HISTORY_FILE = Path.home() / ".local" / "share" / "voiceflow" / "history.jsonl"
CONFIG_FILE = Path.home() / ".config" / "voiceflow" / "config.toml"

# Keys to strip from config responses (never expose to the browser)
_STRIP_KEYS = {"deepgram_api_key", "groq_api_key"}


class DashboardServer:
    """Wraps FastAPI + uvicorn in a background thread."""

    def __init__(self, port: int = 7878):
        self.port = port
        self._start_time = time.time()
        self._state = "idle"

        # asyncio queue filled by broadcast(); the WS sender drains it
        self._queue: asyncio.Queue[dict] = asyncio.Queue()

        # Set of active WebSocket send-coroutines (populated at runtime)
        self._ws_clients: set = set()

        # The asyncio event loop of the main process (set in start())
        self._main_loop: asyncio.AbstractEventLoop | None = None

        self._thread: threading.Thread | None = None
        self._app = self._build_app()

    # ------------------------------------------------------------------
    # Public API (called from main.py)
    # ------------------------------------------------------------------

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        """Start uvicorn in a daemon thread."""
        self._main_loop = loop
        self._thread = threading.Thread(
            target=self._run_server, name="dashboard", daemon=True
        )
        self._thread.start()
        log.info("Dashboard started on http://localhost:%d", self.port)

    def stop(self) -> None:
        # uvicorn in daemon thread will die with the process; nothing extra needed
        pass

    def set_state(self, state: str) -> None:
        self._state = state

    async def broadcast(self, event: dict) -> None:
        """Push an event to all connected WebSocket clients (async-safe)."""
        if event.get("type") == "status":
            self._state = event.get("state", self._state)
        # Schedule delivery on the server's own loop (which may differ)
        if self._main_loop and not self._main_loop.is_closed():
            self._main_loop.call_soon_threadsafe(self._queue.put_nowait, event)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _build_app(self):
        from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
        from fastapi.middleware.cors import CORSMiddleware
        from fastapi.responses import FileResponse, JSONResponse
        import toml

        app = FastAPI(title="VoiceFlow Dashboard", docs_url=None, redoc_url=None)

        app.add_middleware(
            CORSMiddleware,
            allow_origins=["http://localhost", f"http://localhost:{self.port}", "http://127.0.0.1", f"http://127.0.0.1:{self.port}"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        # ---- REST routes ----

        @app.get("/")
        async def index():
            html = STATIC_DIR / "index.html"
            if html.exists():
                return FileResponse(str(html))
            return JSONResponse({"error": "index.html not found"}, status_code=404)

        @app.get("/api/status")
        async def api_status():
            return {
                "state": self._state,
                "uptime_s": int(time.time() - self._start_time),
                "stt_backend": "deepgram",
                "llm_backend": "groq",
                "version": "0.1.0",
            }

        @app.get("/api/history")
        async def api_history():
            entries = []
            if HISTORY_FILE.exists():
                try:
                    lines = HISTORY_FILE.read_text().splitlines()
                    for line in lines[-100:]:
                        line = line.strip()
                        if line:
                            entries.append(json.loads(line))
                except Exception as e:
                    log.error("Failed to read history: %s", e)
            return {"entries": list(reversed(entries))}

        @app.get("/api/config")
        async def api_config():
            result: dict[str, Any] = {}
            if CONFIG_FILE.exists():
                try:
                    result = toml.load(CONFIG_FILE)
                except Exception as e:
                    log.error("Failed to read config: %s", e)
            # Strip API keys
            for k in _STRIP_KEYS:
                result.pop(k, None)
            return result

        @app.post("/api/config")
        async def api_config_post(body: dict):
            # Strip keys if someone tries to post them
            for k in _STRIP_KEYS:
                body.pop(k, None)
            try:
                import toml as toml_lib
                CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
                # Load existing, merge, write
                existing: dict = {}
                if CONFIG_FILE.exists():
                    existing = toml_lib.load(CONFIG_FILE)
                _deep_merge(existing, body)
                CONFIG_FILE.write_text(toml_lib.dumps(existing))
                return {"ok": True}
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        @app.post("/api/action")
        async def api_action(body: dict):
            action = body.get("action", "")
            if action not in ("start", "stop", "restart"):
                raise HTTPException(status_code=400, detail="Invalid action")
            import subprocess
            try:
                result = subprocess.run(
                    ["systemctl", "--user", action, "voiceflow.service"],
                    capture_output=True, text=True, timeout=10,
                )
                return {"ok": result.returncode == 0, "output": result.stdout + result.stderr}
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        # ---- WebSocket ----

        @app.websocket("/ws")
        async def ws_endpoint(websocket: WebSocket):
            await websocket.accept()
            send_queue: asyncio.Queue[dict] = asyncio.Queue()
            self._ws_clients.add(send_queue)
            log.debug("WebSocket client connected")

            async def sender():
                while True:
                    msg = await send_queue.get()
                    try:
                        await websocket.send_text(json.dumps(msg))
                    except Exception:
                        break

            async def receiver():
                try:
                    while True:
                        raw = await websocket.receive_text()
                        data = json.loads(raw)
                        if data.get("type") == "ping":
                            await websocket.send_text(json.dumps({"type": "pong"}))
                except WebSocketDisconnect:
                    pass
                except Exception:
                    pass

            # Fan events from the global queue to this client's queue
            async def fan_out():
                while True:
                    event = await self._queue.get()
                    # Broadcast to all clients
                    dead = set()
                    for q in self._ws_clients:
                        try:
                            q.put_nowait(event)
                        except asyncio.QueueFull:
                            dead.add(q)
                    for q in dead:
                        self._ws_clients.discard(q)

            # Only one fan_out task should exist; start it lazily
            if not hasattr(app.state, "_fan_task") or app.state._fan_task.done():
                app.state._fan_task = asyncio.create_task(fan_out())

            try:
                await asyncio.gather(sender(), receiver())
            finally:
                self._ws_clients.discard(send_queue)
                log.debug("WebSocket client disconnected")

        return app

    def _run_server(self) -> None:
        import uvicorn
        uvicorn.run(
            self._app,
            host="127.0.0.1",
            port=self.port,
            log_level="warning",
            access_log=False,
        )


def _deep_merge(base: dict, override: dict) -> None:
    """Recursively merge override into base in-place."""
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
