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
OPEN_SUPPORT_TICKETS = Gauge("baize_open_support_tickets", "Open or in-progress customer support tickets.")
EMOTION_HITS = Gauge("baize_emotion_hits", "Persisted dialogue emotion counts.", ("emotion",))
CONTENT_SAFETY_CHECKS = Counter(
    "baize_content_safety_checks_total",
    "Content safety checks by direction, action, category and provider.",
    ("direction", "action", "category", "provider"),
)
CONTENT_SAFETY_DURATION = Histogram(
    "baize_content_safety_check_duration_seconds",
    "Content safety decision latency.",
    ("direction", "provider"),
)
CONTENT_SAFETY_PROVIDER_ERRORS = Counter(
    "baize_content_safety_provider_errors_total",
    "Content safety provider errors and upstream guard blocks.",
    ("provider",),
)
APP_CRASH_REPORTS = Counter(
    "baize_app_crash_reports_total",
    "Crash diagnostics uploaded by signed-in mobile apps.",
    ("platform", "error_type"),
)
APP_API_REPORTS = Counter(
    "baize_app_api_reports_total",
    "API request observations uploaded by signed-in mobile apps.",
    ("platform", "route", "status_class"),
)
APP_API_DURATION = Histogram(
    "baize_app_api_duration_seconds",
    "API latency observed by signed-in mobile apps.",
    ("platform", "route"),
)


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


def content_safety_checked(
    direction: str,
    action: str,
    category: str,
    provider: str,
    latency_seconds: float,
) -> None:
    CONTENT_SAFETY_CHECKS.labels(
        direction=direction or "unknown",
        action=action or "unknown",
        category=category or "none",
        provider=provider or "unknown",
    ).inc()
    CONTENT_SAFETY_DURATION.labels(
        direction=direction or "unknown", provider=provider or "unknown"
    ).observe(max(0.0, latency_seconds))


def content_safety_provider_error(provider: str) -> None:
    CONTENT_SAFETY_PROVIDER_ERRORS.labels(provider=provider or "unknown").inc()


def app_crash_reported(platform: str, error_type: str) -> None:
    APP_CRASH_REPORTS.labels(
        platform=platform or "unknown", error_type=(error_type or "unknown")[:120]
    ).inc()


def app_api_reported(
    platform: str, route: str, status_code: int | None, duration_ms: float
) -> None:
    code = int(status_code or 0)
    status_class = f"{code // 100}xx" if 100 <= code <= 599 else "error"
    clean_route = (route or "unknown")[:160]
    APP_API_REPORTS.labels(
        platform=platform or "unknown", route=clean_route, status_class=status_class
    ).inc()
    APP_API_DURATION.labels(
        platform=platform or "unknown", route=clean_route
    ).observe(max(0.0, float(duration_ms or 0)) / 1000.0)


def set_sqlite_health(ok: bool) -> None:
    SQLITE_HEALTH.set(1 if ok else 0)


def set_business_snapshot(snapshot: dict) -> None:
    USERS.set(snapshot.get("users", 0))
    BOUND_DEVICES.set(snapshot.get("bound_devices", 0))
    STORED_DIALOGUES.set(snapshot.get("dialogues", 0))
    STORED_DIARIES.set(snapshot.get("diaries", 0))
    STORED_ENERGY_SPENT.set(snapshot.get("energy_consumed", 0))
    OPEN_SUPPORT_TICKETS.set(snapshot.get("open_tickets", 0))
    for emotion, count in (snapshot.get("emotion_hits") or {}).items():
        EMOTION_HITS.labels(emotion=emotion).set(count)


def metrics_payload() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST
