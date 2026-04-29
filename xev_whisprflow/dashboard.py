"""
voiceflow/dashboard.py

FastAPI dashboard server. Runs uvicorn in a background thread.
Events are delivered from the main asyncio loop to WebSocket clients
via a thread-safe queue that uvicorn's own loop drains.
"""
from __future__ import annotations

import asyncio
import json
import logging
import queue
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
HISTORY_FILE = Path.home() / ".local" / "share" / "xev-whisprflow" / "history.jsonl"
CONFIG_FILE = Path.home() / ".config" / "xev-whisprflow" / "config.toml"
_STRIP_KEYS = {"deepgram_api_key", "groq_api_key"}


class DashboardServer:
    def __init__(self, port: int = 7878):
        self.port = port
        self._start_time = time.time()
        self._state = "idle"
        # Thread-safe queue: main loop puts events, uvicorn loop drains them
        self._event_queue: queue.SimpleQueue[dict] = queue.SimpleQueue()
        self._ws_clients: set[asyncio.Queue] = set()
        self._clients_lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="dashboard", daemon=True)
        self._thread.start()
        log.info("Dashboard running at http://localhost:%d", self.port)

    def stop(self) -> None:
        pass

    def set_state(self, state: str) -> None:
        self._state = state

    def broadcast(self, event: dict) -> None:
        """Thread-safe. Call from any thread/loop."""
        if event.get("type") == "status":
            self._state = event.get("state", self._state)
        self._event_queue.put_nowait(event)

    def _run(self) -> None:
        import uvicorn
        self._kill_existing()
        app = self._build_app()
        config = uvicorn.Config(app, host="127.0.0.1", port=self.port,
                                log_level="warning", access_log=False,
                                loop="asyncio")
        server = uvicorn.Server(config)

        async def run_with_fan_out():
            asyncio.create_task(self._fan_out_loop())
            await server.serve()

        asyncio.run(run_with_fan_out())

    def _kill_existing(self) -> None:
        """Kill any process already using our port."""
        import socket
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", self.port)) != 0:
                return  # port is free
        try:
            import subprocess
            result = subprocess.run(
                ["fuser", "-k", f"{self.port}/tcp"],
                capture_output=True, timeout=3,
            )
            if result.returncode == 0:
                import time; time.sleep(0.3)
                log.debug("Killed existing process on port %d", self.port)
        except Exception:
            pass

    async def _fan_out_loop(self) -> None:
        """Drain the thread-safe queue and push to all WS clients."""
        loop = asyncio.get_running_loop()
        while True:
            try:
                event = await loop.run_in_executor(None, self._event_queue.get)
                with self._clients_lock:
                    clients = list(self._ws_clients)
                for q in clients:
                    try:
                        q.put_nowait(event)
                    except asyncio.QueueFull:
                        pass
            except Exception:
                pass

    def _build_app(self):
        from fastapi import FastAPI, WebSocket, WebSocketDisconnect
        from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
        import toml

        app = FastAPI(docs_url=None, redoc_url=None)

        @app.get("/")
        async def index():
            f = STATIC_DIR / "index.html"
            return FileResponse(str(f)) if f.exists() else JSONResponse({"error": "not found"}, status_code=404)

        @app.get("/api/status")
        async def api_status():
            return {
                "state": self._state,
                "uptime_s": int(time.time() - self._start_time),
                "version": "0.1.0",
            }

        @app.get("/api/history")
        async def api_history():
            entries = []
            if HISTORY_FILE.exists():
                try:
                    for line in HISTORY_FILE.read_text().splitlines()[-100:]:
                        line = line.strip()
                        if line:
                            entries.append(json.loads(line))
                except Exception as e:
                    log.error("history read error: %s", e)
            return {"entries": list(reversed(entries))}

        @app.get("/api/config")
        async def api_config():
            result: dict[str, Any] = {}
            if CONFIG_FILE.exists():
                try:
                    result = toml.load(CONFIG_FILE)
                except Exception:
                    pass
            for k in _STRIP_KEYS:
                result.pop(k, None)
            # Inject available audio devices
            try:
                import sounddevice as sd
                devices = sd.query_devices()
                result["_audio_devices"] = [
                    d["name"] for d in devices if d["max_input_channels"] > 0
                ]
            except Exception:
                result["_audio_devices"] = []
            return result

        @app.post("/api/config")
        async def api_config_post(body: dict):
            for k in _STRIP_KEYS:
                body.pop(k, None)
            body.pop("_audio_devices", None)
            try:
                existing: dict = {}
                if CONFIG_FILE.exists():
                    existing = toml.load(CONFIG_FILE)
                _deep_merge(existing, body)
                CONFIG_FILE.write_text(toml.dumps(existing))
                return {"ok": True}
            except Exception as e:
                return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

        @app.post("/api/action")
        async def api_action(body: dict):
            action = body.get("action", "")
            if action not in ("start", "stop", "restart"):
                return JSONResponse({"error": "invalid action"}, status_code=400)
            try:
                r = subprocess.run(
                    ["systemctl", "--user", action, "xev_whisprflow.service"],
                    capture_output=True, text=True, timeout=10,
                )
                return {"ok": r.returncode == 0, "output": r.stdout + r.stderr}
            except Exception as e:
                return JSONResponse({"error": str(e)}, status_code=500)

        @app.websocket("/ws")
        async def ws_endpoint(websocket: WebSocket):
            await websocket.accept()
            q: asyncio.Queue[dict] = asyncio.Queue(maxsize=64)
            with self._clients_lock:
                self._ws_clients.add(q)

            # Send current state immediately on connect
            q.put_nowait({"type": "status", "state": self._state,
                          "uptime_s": int(time.time() - self._start_time)})

            async def sender():
                while True:
                    msg = await q.get()
                    await websocket.send_text(json.dumps(msg))

            async def receiver():
                try:
                    while True:
                        raw = await websocket.receive_text()
                        if json.loads(raw).get("type") == "ping":
                            await websocket.send_text('{"type":"pong"}')
                except (WebSocketDisconnect, Exception):
                    pass

            try:
                await asyncio.gather(sender(), receiver())
            except Exception:
                pass
            finally:
                with self._clients_lock:
                    self._ws_clients.discard(q)

        return app


def _deep_merge(base: dict, override: dict) -> None:
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
