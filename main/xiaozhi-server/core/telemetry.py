"""Prometheus metrics for the Baize direct Python deployment."""

from __future__ import annotations

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest


HTTP_REQUESTS = Counter("baize_http_requests_total", "HTTP requests handled by the Baize service.", ("method", "route", "status"))
HTTP_DURATION = Histogram("baize_http_request_duration_seconds", "HTTP request duration in seconds.", ("method", "route"))
WEBSOCKET_CONNECTIONS = Gauge("baize_websocket_connections_active", "Currently active device WebSocket connections.")
WEBSOCKET_CONNECTION_TOTAL = Counter("baize_websocket_connections_total", "Completed device WebSocket connections.", ("result",))
DIALOGUES = Counter("baize_dialogues_total", "Persisted Baize dialogues.", ("source", "emotion"))
DIARIES = Counter("baize_diaries_generated_total", "Successfully generated or refreshed diaries.")
ENERGY_SPENT = Counter("baize_energy_spent_total", "Spirit power consumed by successful operations.", ("reason",))
SQLITE_HEALTH = Gauge("baize_sqlite_healthy", "Whether the App MVP SQLite database is readable and writable.")
USERS = Gauge("baize_users", "Total App MVP users.")
BOUND_DEVICES = Gauge("baize_bound_devices", "Total App MVP device bindings.")
STORED_DIALOGUES = Gauge("baize_stored_dialogues", "Total persisted App MVP dialogues.")
STORED_DIARIES = Gauge("baize_stored_diaries", "Total persisted App MVP diaries.")
STORED_ENERGY_SPENT = Gauge("baize_stored_energy_spent", "Total recorded spirit power consumption.")
EMOTION_HITS = Gauge("baize_emotion_hits", "Persisted dialogue emotion counts.", ("emotion",))


def observe_http_request(method: str, route: str, status: int, duration_seconds: float) -> None:
    HTTP_REQUESTS.labels(method=method, route=route, status=str(status)).inc()
    HTTP_DURATION.labels(method=method, route=route).observe(max(duration_seconds, 0.0))


def websocket_opened() -> None:
    WEBSOCKET_CONNECTIONS.inc()


def websocket_closed(result: str) -> None:
    WEBSOCKET_CONNECTIONS.dec()
    WEBSOCKET_CONNECTION_TOTAL.labels(result=result if result in {"success", "error"} else "error").inc()


def dialogue_persisted(source: str, emotion: str) -> None:
    DIALOGUES.labels(source=source or "unknown", emotion=emotion or "neutral").inc()


def diary_generated() -> None:
    DIARIES.inc()


def energy_spent(amount: int, reason: str) -> None:
    if amount > 0:
        ENERGY_SPENT.labels(reason=reason or "unknown").inc(amount)


def set_sqlite_health(ok: bool) -> None:
    SQLITE_HEALTH.set(1 if ok else 0)


def set_business_snapshot(snapshot: dict) -> None:
    USERS.set(snapshot.get("users", 0))
    BOUND_DEVICES.set(snapshot.get("bound_devices", 0))
    STORED_DIALOGUES.set(snapshot.get("dialogues", 0))
    STORED_DIARIES.set(snapshot.get("diaries", 0))
    STORED_ENERGY_SPENT.set(snapshot.get("energy_consumed", 0))
    for emotion, count in (snapshot.get("emotion_hits") or {}).items():
        EMOTION_HITS.labels(emotion=emotion).set(count)


def metrics_payload() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST
