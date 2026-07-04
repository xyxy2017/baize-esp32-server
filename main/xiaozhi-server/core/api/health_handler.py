import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any, Dict

from aiohttp import web

from core.api.app_demo_store import app_mvp_db_path_from_config, ensure_db
from core.api.base_handler import BaseHandler


class HealthHandler(BaseHandler):
    """Lightweight unauthenticated health endpoint for direct Python deploys."""

    def __init__(self, config: dict):
        super().__init__(config)
        self.started_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        self.started_monotonic = time.monotonic()

    def routes(self):
        return [
            web.get("/healthz", self.handle_healthz),
            web.options("/healthz", self.handle_options),
        ]

    async def handle_healthz(self, request):
        db = self._sqlite_health()
        payload: Dict[str, Any] = {
            "status": "ok" if db["ok"] else "degraded",
            "service": "baize-xiaozhi-server",
            "started_at": self.started_at,
            "uptime_seconds": int(time.monotonic() - self.started_monotonic),
            "http_port": int(self.config.get("server", {}).get("http_port", 8003)),
            "websocket_port": int(self.config.get("server", {}).get("port", 8000)),
            "websocket": self.config.get("server", {}).get("websocket", ""),
            "sqlite": db,
        }
        response = web.Response(
            text=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            content_type="application/json",
            status=200 if db["ok"] else 503,
        )
        self._add_cors_headers(response)
        return response

    def _sqlite_health(self) -> Dict[str, Any]:
        db_path = app_mvp_db_path_from_config(self.config)
        try:
            ensure_db(db_path)
            conn = sqlite3.connect(db_path)
            try:
                conn.execute("CREATE TABLE IF NOT EXISTS health_checks(id INTEGER PRIMARY KEY CHECK(id = 1), checked_at TEXT)")
                conn.execute(
                    "INSERT INTO health_checks(id, checked_at) VALUES (1, ?) "
                    "ON CONFLICT(id) DO UPDATE SET checked_at = excluded.checked_at",
                    (datetime.now(timezone.utc).replace(microsecond=0).isoformat(),),
                )
                conn.commit()
                conn.execute("SELECT checked_at FROM health_checks WHERE id = 1").fetchone()
            finally:
                conn.close()
            return {"ok": True, "path": db_path, "writable": os.access(os.path.dirname(db_path) or ".", os.W_OK)}
        except Exception as e:
            return {"ok": False, "path": db_path, "error": type(e).__name__, "message": str(e)}
