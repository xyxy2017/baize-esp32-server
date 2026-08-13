import json
import os
import re
import hashlib
import hmac
import secrets
import sqlite3
import uuid
import glob
import math
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict
from zoneinfo import ZoneInfo


DEMO_TOKEN = "demo-token"
DEMO_USER_ID = "demo_user"
DEMO_DEVICE_CODE = "123456"
DEMO_DEVICE_ID = "baize_dev_001"
DEFAULT_INVITE_CODE = "BAIZE-MVP"
INITIAL_SPIRIT_POWER = 120
SPIRIT_POWER_LIMIT = 120
SPIRIT_POWER_RECOVERY_PER_HOUR = 5
SPIRIT_POWER_PER_MINUTE = 5
SPIRIT_DEW_AMOUNT = 30
SPIRIT_DEW_MAX_INVENTORY = 7
PRODUCT_TIMEZONE = ZoneInfo("Asia/Shanghai")
PRODUCT_DAY_BOUNDARY_HOUR = 4
# Legacy aliases keep the existing SQLite schema and old clients migratable.
INITIAL_ENERGY = INITIAL_SPIRIT_POWER
DAILY_ENERGY_LIMIT = SPIRIT_POWER_LIMIT
PASSWORD_MIN_LENGTH = 6
PHONE_PATTERN = re.compile(r"^1[3-9]\d{9}$")
USER_ROLE = "user"
ADMIN_ROLE = "admin"
VALID_ROLES = {USER_ROLE, ADMIN_ROLE}
SMS_PURPOSE_LOGIN = "login"
SMS_PURPOSE_REGISTER = "register"
SMS_PURPOSE_RESET_PASSWORD = "reset_password"
SMS_PURPOSES = {
    SMS_PURPOSE_LOGIN,
    SMS_PURPOSE_REGISTER,
    SMS_PURPOSE_RESET_PASSWORD,
}
SMS_CODE_LENGTH = 6
SMS_CODE_MAX_ATTEMPTS = 5
SMS_DEFAULT_TTL_SECONDS = 300
SMS_DEFAULT_RESEND_INTERVAL_SECONDS = 60
SMS_DEFAULT_MAX_PER_PHONE_HOUR = 5
SMS_DEFAULT_MAX_PER_PHONE_DAY = 10
SMS_DEFAULT_MAX_PER_IP_HOUR = 30
DIARY_AUTO_EVENING_HOUR = 22
DIARY_AUTO_EVENING_MINUTE = 30
DIARY_AUTO_QUIET_MINUTES = 30
DIARY_AUTO_SCAN_INTERVAL_SECONDS = 300
DIARY_AUTO_LOOKBACK_DAYS = 7

MVP_EMOTIONS = {"neutral", "happy", "thinking", "surprised", "sad", "sleepy", "confused"}
EMOTION_ALIASES = {
    "laughing": "happy",
    "funny": "happy",
    "loving": "happy",
    "embarrassed": "surprised",
    "shocked": "surprised",
    "winking": "happy",
    "cool": "happy",
    "relaxed": "happy",
    "delicious": "happy",
    "confident": "happy",
    "silly": "happy",
    "angry": "sad",
    "crying": "sad",
}
DIARY_GENERATION_PROMPT = """你是白泽幼灵，请根据当天真实对话历史写一篇白泽视角日记。

目标风格参考：
- 像小宠物睡前写下今天和小伙伴发生的事，而不是客服摘要。
- 用白泽第一视角写“我听见、我当时、后来、现在”，称呼用户为“你”或“小伙伴”。
- 记录当天具体互动、调试事项、触摸唤醒、白泽自己的小情绪。
- 可以有一点可爱、困意、骄傲、担心和小心愿，但必须来自真实对话，不编造天气、地点或未发生事件。
- 分成 2 到 4 段，读起来像 App 里的日记正文。
- 不要输出“用户说/白泽回应”的流水账格式，不要在正文逐句引用原始对话。
"""
EMOJI_EMOTION_MAP = {
    "😶": "neutral",
    "🙂": "happy",
    "😆": "happy",
    "😂": "happy",
    "😔": "sad",
    "😠": "sad",
    "😭": "sad",
    "😍": "happy",
    "😳": "surprised",
    "😲": "surprised",
    "😱": "surprised",
    "🤔": "thinking",
    "😉": "happy",
    "😎": "happy",
    "😌": "happy",
    "🤤": "happy",
    "😘": "happy",
    "😏": "happy",
    "😴": "sleepy",
    "😜": "happy",
    "🙄": "confused",
}

EMOTION_PREFIX_PATTERN = re.compile(r"^[\s]*(?:[😶🙂😆😂😔😠😭😍😳😲😱🤔😉😎😌🤤😘😏😴😜🙄]\s*)+")
ACTION_PARENTHETICAL_PATTERN = re.compile(r"[（(][^（）()\[\]【】\n]{1,80}[）)]")
ACTION_BRACKET_PATTERN = re.compile(r"[\[【][^\[\]【】（）()\n]{1,80}[\]】]")
BROKEN_LEADING_ACTION_PATTERN = re.compile(r"^\s*[^，。！？!?；;：:\n]{1,40}[）)\]】]\s*")


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        super().__exit__(exc_type, exc_value, traceback)
        self.close()


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def product_day_key(value: datetime | None = None) -> str:
    current = (value or datetime.now(timezone.utc)).astimezone(PRODUCT_TIMEZONE)
    return (current - timedelta(hours=PRODUCT_DAY_BOUNDARY_HOUR)).date().isoformat()


def today_key() -> str:
    return product_day_key()


def diary_date_key(value: datetime | None = None) -> str:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(PRODUCT_TIMEZONE).date().isoformat()


def diary_auto_generation_settings(config: dict | None = None) -> Dict[str, Any]:
    values = ((config or {}).get("app_mvp", {}) or {}).get(
        "diary_auto_generation", {}
    ) or {}

    def bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
        try:
            return min(maximum, max(minimum, int(values.get(name, default))))
        except (TypeError, ValueError):
            return default

    return {
        "enabled": bool(values.get("enabled", True)),
        "evening_hour": bounded_int(
            "evening_hour", DIARY_AUTO_EVENING_HOUR, 0, 23
        ),
        "evening_minute": bounded_int(
            "evening_minute", DIARY_AUTO_EVENING_MINUTE, 0, 59
        ),
        "quiet_period_minutes": bounded_int(
            "quiet_period_minutes", DIARY_AUTO_QUIET_MINUTES, 1, 24 * 60
        ),
        "scan_interval_seconds": bounded_int(
            "scan_interval_seconds", DIARY_AUTO_SCAN_INTERVAL_SECONDS, 30, 3600
        ),
        "lookback_days": bounded_int(
            "lookback_days", DIARY_AUTO_LOOKBACK_DAYS, 1, 30
        ),
    }


def spirit_power_settings(config: dict | None = None) -> Dict[str, int]:
    config = config or {}
    values = (config.get("app_mvp", {}) or {}).get("spirit_power", {}) or {}

    def positive_int(name: str, default: int) -> int:
        try:
            return max(1, int(values.get(name, default)))
        except (TypeError, ValueError):
            return default

    limit = positive_int("max", SPIRIT_POWER_LIMIT)
    return {
        "max": limit,
        "initial": min(limit, positive_int("initial", limit)),
        "recovery_per_hour": positive_int(
            "recovery_per_hour", SPIRIT_POWER_RECOVERY_PER_HOUR
        ),
        "conversation_cost": positive_int(
            "conversation_cost", SPIRIT_POWER_PER_MINUTE
        ),
        "spirit_dew_amount": positive_int("spirit_dew_amount", SPIRIT_DEW_AMOUNT),
    }


def spirit_power_conversation_cost(config: dict | None = None) -> int:
    return spirit_power_settings(config)["conversation_cost"]


def spirit_power_cost_for_seconds(
    valid_user_audio_seconds: float | int | None, config: dict | None = None
) -> int:
    seconds = max(0.0, float(valid_user_audio_seconds or 0))
    per_minute = spirit_power_conversation_cost(config)
    return max(per_minute, math.ceil(seconds / 60) * per_minute)


def app_mvp_db_path_from_config(config: dict) -> str:
    if config.get("app_mvp", {}).get("db_path"):
        return config["app_mvp"]["db_path"]
    if config.get("app_demo", {}).get("db_path"):
        return config["app_demo"]["db_path"]
    if config.get("app_demo", {}).get("state_path"):
        return f"{config['app_demo']['state_path']}.sqlite3"
    return os.path.join(os.getcwd(), "data", "app_mvp.sqlite3")


def state_path_from_config(config: dict) -> str:
    return app_mvp_db_path_from_config(config)


def firmware_bin_dir_from_config(config: dict) -> str:
    return config.get("app_mvp", {}).get("firmware_bin_dir") or os.path.join(os.getcwd(), "data", "bin")


def _parse_firmware_version(version: str) -> tuple[int, ...]:
    parts = re.findall(r"\d+", version or "")
    return tuple(int(part) for part in parts) if parts else (0,)


def _is_higher_firmware_version(candidate: str, current: str) -> bool:
    candidate_parts = _parse_firmware_version(candidate)
    current_parts = _parse_firmware_version(current)
    max_length = max(len(candidate_parts), len(current_parts))
    for index in range(max_length):
        candidate_value = candidate_parts[index] if index < len(candidate_parts) else 0
        current_value = current_parts[index] if index < len(current_parts) else 0
        if candidate_value > current_value:
            return True
        if candidate_value < current_value:
            return False
    return False


def latest_firmware_version_for_model(config: dict, model: str) -> str | None:
    clean_model = (model or "").strip()
    if not clean_model:
        return None
    bin_dir = firmware_bin_dir_from_config(config)
    pattern = os.path.join(bin_dir, f"{clean_model}_*.bin")
    versions = []
    for path in glob.glob(pattern):
        filename = os.path.basename(path)
        match = re.match(rf"^{re.escape(clean_model)}_([0-9][A-Za-z0-9\.\-_]*)\.bin$", filename)
        if match:
            versions.append(match.group(1))
    if not versions:
        return None
    versions.sort(key=_parse_firmware_version, reverse=True)
    return versions[0]


def db_path_from_state_path(path: str) -> str:
    return f"{path}.sqlite3" if path.endswith(".json") else path


def normalize_emotion(emotion: str | None, fallback: str = "neutral") -> str:
    value = (emotion or "").strip()
    if value in MVP_EMOTIONS:
        return value
    if value in EMOTION_ALIASES:
        return EMOTION_ALIASES[value]
    return fallback if fallback in MVP_EMOTIONS else "neutral"


def clean_baize_text(text: str) -> str:
    cleaned = EMOTION_PREFIX_PATTERN.sub("", (text or "").strip()).strip()
    previous = None
    while previous != cleaned:
        previous = cleaned
        cleaned = ACTION_PARENTHETICAL_PATTERN.sub("", cleaned)
        cleaned = ACTION_BRACKET_PATTERN.sub("", cleaned)
        cleaned = BROKEN_LEADING_ACTION_PATTERN.sub("", cleaned)
        cleaned = EMOTION_PREFIX_PATTERN.sub("", cleaned).strip()
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return re.sub(r"\s+([，。！？!?；;：:])", r"\1", cleaned)


def infer_emotion(text: str, fallback: str = "neutral") -> str:
    for char in (text or "").strip():
        if not char.isspace():
            return normalize_emotion(EMOJI_EMOTION_MAP.get(char), fallback)
    return normalize_emotion(fallback)


def is_legacy_xiaozhi_dialogue(item: Dict[str, Any]) -> bool:
    text = str(item.get("baize_text", ""))
    legacy_markers = (
        "小智",
        "小志",
        "台湾腔",
        "484",
        "齁～",
        "我素",
        "珍奶",
        "开心捏",
        "考官大人",
        "主人",
    )
    return any(marker in text for marker in legacy_markers)


def _connect(db_path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path, factory=ClosingConnection)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _execute_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            nickname TEXT NOT NULL,
            login_type TEXT NOT NULL,
            invite_code TEXT NOT NULL,
            created_at TEXT NOT NULL,
            last_login_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS auth_tokens (
            token TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS dashboard_admins (
            username TEXT PRIMARY KEY,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            last_login_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS dashboard_tokens (
            token TEXT PRIMARY KEY,
            username TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_dashboard_tokens_username
            ON dashboard_tokens(username);
        CREATE TABLE IF NOT EXISTS sms_verification_requests (
            id TEXT PRIMARY KEY,
            phone TEXT NOT NULL,
            purpose TEXT NOT NULL,
            code_hash TEXT NOT NULL,
            request_ip TEXT,
            provider_request_id TEXT,
            status TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            consumed_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_sms_verification_phone_created
            ON sms_verification_requests(phone, created_at);
        CREATE INDEX IF NOT EXISTS idx_sms_verification_ip_created
            ON sms_verification_requests(request_ip, created_at);
        CREATE TABLE IF NOT EXISTS devices (
            id TEXT PRIMARY KEY,
            device_code TEXT NOT NULL UNIQUE,
            display_name TEXT NOT NULL,
            source_device_id TEXT,
            client_id TEXT,
            model TEXT,
            online_status TEXT NOT NULL DEFAULT 'unknown',
            battery_percent INTEGER,
            activity_status TEXT NOT NULL DEFAULT 'unknown',
            firmware_version TEXT NOT NULL DEFAULT '0.1.0-demo',
            last_online_at TEXT,
            current_version TEXT NOT NULL DEFAULT '0.1.0-demo',
            latest_version TEXT NOT NULL DEFAULT '0.1.0-demo',
            update_available INTEGER NOT NULL DEFAULT 0,
            release_note TEXT NOT NULL DEFAULT '等待设备版本上报'
        );
        CREATE TABLE IF NOT EXISTS user_device_bindings (
            user_id TEXT NOT NULL,
            device_id TEXT NOT NULL,
            bound_at TEXT NOT NULL,
            PRIMARY KEY(user_id, device_id)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_user_device_bindings_device_unique
            ON user_device_bindings(device_id);
        CREATE TABLE IF NOT EXISTS device_settings (
            device_id TEXT PRIMARY KEY,
            baize_nickname TEXT NOT NULL,
            user_call_name TEXT NOT NULL,
            personality_mode TEXT NOT NULL,
            tts_voice TEXT NOT NULL DEFAULT 'Sambert 知颖',
            speaker_volume INTEGER,
            screen_brightness INTEGER
        );
        CREATE TABLE IF NOT EXISTS dialogues (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            device_id TEXT NOT NULL,
            source_device_id TEXT,
            session_id TEXT,
            user_text TEXT NOT NULL,
            baize_text TEXT NOT NULL,
            emotion TEXT NOT NULL,
            user_audio_seconds REAL NOT NULL DEFAULT 0,
            spirit_power_cost INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_dialogues_owner_created_at
            ON dialogues(user_id, device_id, created_at);
        CREATE TABLE IF NOT EXISTS diaries (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            device_id TEXT NOT NULL,
            date TEXT NOT NULL,
            title TEXT NOT NULL,
            summary TEXT NOT NULL,
            primary_emotion TEXT NOT NULL,
            dialogue_count INTEGER NOT NULL,
            quotes_json TEXT NOT NULL,
            baize_note TEXT NOT NULL,
            generated_at TEXT NOT NULL,
            UNIQUE(user_id, device_id, date)
        );
        CREATE TABLE IF NOT EXISTS memories (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            device_id TEXT NOT NULL,
            category TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL,
            disabled_at TEXT
        );
        CREATE TABLE IF NOT EXISTS energy_accounts (
            user_id TEXT PRIMARY KEY,
            current_energy INTEGER NOT NULL,
            daily_limit INTEGER NOT NULL,
            updated_at TEXT NOT NULL,
            last_recovered_on TEXT NOT NULL,
            last_hourly_recovered_at TEXT
        );
        CREATE TABLE IF NOT EXISTS energy_events (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            device_id TEXT,
            delta INTEGER NOT NULL,
            reason TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS spirit_power_items (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            item_type TEXT NOT NULL,
            amount INTEGER NOT NULL,
            granted_on TEXT NOT NULL,
            expires_on TEXT NOT NULL,
            used_at TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS spirit_power_checkins (
            user_id TEXT NOT NULL,
            product_day TEXT NOT NULL,
            item_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY(user_id, product_day)
        );
        CREATE TABLE IF NOT EXISTS intimacy_accounts (
            user_id TEXT NOT NULL,
            device_id TEXT NOT NULL,
            score INTEGER NOT NULL,
            level TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_interaction_on TEXT,
            streak_days INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(user_id, device_id)
        );
        CREATE TABLE IF NOT EXISTS intimacy_events (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            device_id TEXT NOT NULL,
            delta INTEGER NOT NULL,
            reason TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS tts_candidates (
            id TEXT PRIMARY KEY,
            provider TEXT NOT NULL,
            voice TEXT NOT NULL,
            price_note TEXT,
            latency_note TEXT,
            score INTEGER,
            is_default INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS emotion_stats (
            id TEXT PRIMARY KEY,
            user_id TEXT,
            device_id TEXT,
            emotion TEXT NOT NULL,
            source TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS app_telemetry_events (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            platform TEXT NOT NULL,
            app_version TEXT,
            route TEXT,
            method TEXT,
            status_code INTEGER,
            duration_ms REAL,
            error_type TEXT,
            message TEXT,
            details_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_app_telemetry_type_created
            ON app_telemetry_events(event_type, created_at);
        CREATE TABLE IF NOT EXISTS support_tickets (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            device_id TEXT,
            category TEXT NOT NULL,
            subject TEXT NOT NULL,
            message TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            operator_reply TEXT,
            operator_username TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            resolved_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_support_tickets_user_created
            ON support_tickets(user_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_support_tickets_status_updated
            ON support_tickets(status, updated_at DESC);
        """
    )
    _migrate_schema(conn)
    from core.api.app_memory_store import ensure_memory_v2_schema
    from core.content_safety import ensure_content_safety_schema

    ensure_memory_v2_schema(conn)
    ensure_content_safety_schema(conn)
    conn.commit()


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _migrate_schema(conn: sqlite3.Connection) -> None:
    user_columns = _table_columns(conn, "users")
    if "phone" not in user_columns:
        conn.execute("ALTER TABLE users ADD COLUMN phone TEXT")
    if "password_hash" not in user_columns:
        conn.execute("ALTER TABLE users ADD COLUMN password_hash TEXT")
    if "password_updated_at" not in user_columns:
        conn.execute("ALTER TABLE users ADD COLUMN password_updated_at TEXT")
    if "role" not in user_columns:
        conn.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_phone_unique ON users(phone) WHERE phone IS NOT NULL AND phone != ''")
    conn.execute("DROP INDEX IF EXISTS idx_bindings_device_unique")
    device_columns = _table_columns(conn, "devices")
    if "activity_status" not in device_columns:
        conn.execute("ALTER TABLE devices ADD COLUMN activity_status TEXT NOT NULL DEFAULT 'unknown'")
    energy_columns = _table_columns(conn, "energy_accounts")
    if "last_hourly_recovered_at" not in energy_columns:
        conn.execute("ALTER TABLE energy_accounts ADD COLUMN last_hourly_recovered_at TEXT")
    settings_columns = _table_columns(conn, "device_settings")
    if "speaker_volume" not in settings_columns:
        conn.execute("ALTER TABLE device_settings ADD COLUMN speaker_volume INTEGER")
    if "screen_brightness" not in settings_columns:
        conn.execute("ALTER TABLE device_settings ADD COLUMN screen_brightness INTEGER")
    dialogue_columns = _table_columns(conn, "dialogues")
    if "user_audio_seconds" not in dialogue_columns:
        conn.execute(
            "ALTER TABLE dialogues ADD COLUMN user_audio_seconds REAL NOT NULL DEFAULT 0"
        )
    if "spirit_power_cost" not in dialogue_columns:
        conn.execute(
            "ALTER TABLE dialogues ADD COLUMN spirit_power_cost INTEGER NOT NULL DEFAULT 0"
        )
    now = now_iso()
    product_day = product_day_key()
    legacy_accounts = conn.execute(
        "SELECT user_id, current_energy FROM energy_accounts WHERE daily_limit = 30"
    ).fetchall()
    for account in legacy_accounts:
        delta = SPIRIT_POWER_LIMIT - account["current_energy"]
        conn.execute(
            """
            UPDATE energy_accounts
            SET current_energy = ?, daily_limit = ?, updated_at = ?,
                last_recovered_on = ?, last_hourly_recovered_at = ?
            WHERE user_id = ?
            """,
            (SPIRIT_POWER_LIMIT, SPIRIT_POWER_LIMIT, now, product_day, now, account["user_id"]),
        )
        if delta:
            conn.execute(
                "INSERT INTO energy_events(id, user_id, delta, reason, created_at) VALUES (?, ?, ?, 'spirit_power_migration', ?)",
                (f"energy_{uuid.uuid4().hex}", account["user_id"], delta, now),
            )


def normalize_phone(phone: str | None) -> str:
    return re.sub(r"\D", "", phone or "")


def mask_phone(phone: str) -> str:
    phone = normalize_phone(phone)
    if len(phone) < 7:
        return phone
    return f"{phone[:3]}****{phone[-4:]}"


def validate_phone(phone: str) -> str:
    phone = normalize_phone(phone)
    if not PHONE_PATTERN.match(phone):
        raise ValueError("phone 格式不正确")
    return phone


def _review_login_phones_from_config(config: dict) -> set[str]:
    auth_settings = config.get("app_mvp", {}).get("auth", {}) or {}
    raw_values = auth_settings.get("review_login_phones") or []
    if isinstance(raw_values, str):
        raw_values = [raw_values]
    return {normalize_phone(str(item)) for item in raw_values if normalize_phone(str(item))}


def validate_login_phone(config: dict, phone: str) -> str:
    """Allow explicit virtual review IDs for password login only.

    Registration, SMS login, and SMS password reset continue to call
    validate_phone(), so a review ID can never receive a code or enter the
    normal customer phone namespace.
    """
    phone = normalize_phone(phone)
    if PHONE_PATTERN.match(phone) or phone in _review_login_phones_from_config(config):
        return phone
    raise ValueError("phone 格式不正确")


def _admin_phones_from_config(config: dict) -> set[str]:
    raw_values = config.get("app_mvp", {}).get("admin_phones") or []
    if isinstance(raw_values, str):
        raw_values = [raw_values]
    return {normalize_phone(str(item)) for item in raw_values if normalize_phone(str(item))}


def role_for_phone(config: dict, phone: str | None) -> str:
    return ADMIN_ROLE if normalize_phone(phone) in _admin_phones_from_config(config) else USER_ROLE


def sync_configured_admin_roles(config: dict, conn: sqlite3.Connection) -> None:
    admin_phones = _admin_phones_from_config(config)
    if not admin_phones:
        return
    placeholders = ",".join("?" for _ in admin_phones)
    conn.execute(
        f"UPDATE users SET role = ? WHERE phone IN ({placeholders})",
        (ADMIN_ROLE, *sorted(admin_phones)),
    )


def _validate_password(password: str) -> str:
    password = password or ""
    if len(password) < PASSWORD_MIN_LENGTH:
        raise ValueError(f"password 至少 {PASSWORD_MIN_LENGTH} 位")
    return password


def _hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or uuid.uuid4().hex
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120000)
    return f"pbkdf2_sha256${salt}${digest.hex()}"


def _verify_password(password: str, password_hash: str | None) -> bool:
    if not password_hash:
        return False
    try:
        algorithm, salt, expected = password_hash.split("$", 2)
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    actual = _hash_password(password, salt).split("$", 2)[2]
    return hmac.compare_digest(actual, expected)


def _sms_settings(config: dict) -> Dict[str, int]:
    settings = config.get("app_mvp", {}).get("sms", {}) or {}

    def positive_int(name: str, default: int) -> int:
        try:
            value = int(settings.get(name, default))
        except (TypeError, ValueError):
            return default
        return max(1, value)

    return {
        "code_ttl_seconds": positive_int(
            "code_ttl_seconds", SMS_DEFAULT_TTL_SECONDS
        ),
        "resend_interval_seconds": positive_int(
            "resend_interval_seconds", SMS_DEFAULT_RESEND_INTERVAL_SECONDS
        ),
        "max_per_phone_hour": positive_int(
            "max_per_phone_hour", SMS_DEFAULT_MAX_PER_PHONE_HOUR
        ),
        "max_per_phone_day": positive_int(
            "max_per_phone_day", SMS_DEFAULT_MAX_PER_PHONE_DAY
        ),
        "max_per_ip_hour": positive_int(
            "max_per_ip_hour", SMS_DEFAULT_MAX_PER_IP_HOUR
        ),
    }


class SMSRateLimitError(ValueError):
    def __init__(self, retry_after_seconds: int):
        self.retry_after_seconds = max(1, int(retry_after_seconds))
        super().__init__(
            f"验证码发送过于频繁，请在 {self.retry_after_seconds} 秒后重试"
        )


def create_sms_verification_request(
    config: dict,
    phone: str,
    purpose: str,
    request_ip: str | None,
) -> Dict[str, Any]:
    phone = validate_phone(phone)
    if purpose not in SMS_PURPOSES:
        raise ValueError("purpose 不支持")
    request_ip = (request_ip or "").strip()[:64] or None
    settings = _sms_settings(config)
    db_path = app_mvp_db_path_from_config(config)
    ensure_db(db_path)
    current = datetime.now(timezone.utc).replace(microsecond=0)
    now = current.isoformat()
    one_hour_ago = (current - timedelta(hours=1)).isoformat()
    one_day_ago = (current - timedelta(days=1)).isoformat()

    with _connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        latest = conn.execute(
            """
            SELECT created_at FROM sms_verification_requests
            WHERE phone = ? AND status IN ('pending', 'sent')
            ORDER BY created_at DESC LIMIT 1
            """,
            (phone,),
        ).fetchone()
        if latest:
            latest_at = datetime.fromisoformat(latest["created_at"])
            elapsed = int((current - latest_at).total_seconds())
            if elapsed < settings["resend_interval_seconds"]:
                raise SMSRateLimitError(
                    settings["resend_interval_seconds"] - elapsed
                )

        phone_hour_count = conn.execute(
            """
            SELECT COUNT(*) AS c FROM sms_verification_requests
            WHERE phone = ? AND status IN ('pending', 'sent', 'consumed', 'superseded')
              AND created_at >= ?
            """,
            (phone, one_hour_ago),
        ).fetchone()["c"]
        if phone_hour_count >= settings["max_per_phone_hour"]:
            raise SMSRateLimitError(3600)

        phone_day_count = conn.execute(
            """
            SELECT COUNT(*) AS c FROM sms_verification_requests
            WHERE phone = ? AND status IN ('pending', 'sent', 'consumed', 'superseded')
              AND created_at >= ?
            """,
            (phone, one_day_ago),
        ).fetchone()["c"]
        if phone_day_count >= settings["max_per_phone_day"]:
            raise SMSRateLimitError(86400)

        if request_ip:
            ip_hour_count = conn.execute(
                """
                SELECT COUNT(*) AS c FROM sms_verification_requests
                WHERE request_ip = ?
                  AND status IN ('pending', 'sent', 'consumed', 'superseded')
                  AND created_at >= ?
                """,
                (request_ip, one_hour_ago),
            ).fetchone()["c"]
            if ip_hour_count >= settings["max_per_ip_hour"]:
                raise SMSRateLimitError(3600)

        code = f"{secrets.randbelow(10 ** SMS_CODE_LENGTH):0{SMS_CODE_LENGTH}d}"
        request_id = f"sms_{uuid.uuid4().hex}"
        expires_at = (
            current + timedelta(seconds=settings["code_ttl_seconds"])
        ).isoformat()
        conn.execute(
            """
            INSERT INTO sms_verification_requests(
                id, phone, purpose, code_hash, request_ip, status,
                attempts, created_at, expires_at
            ) VALUES (?, ?, ?, ?, ?, 'pending', 0, ?, ?)
            """,
            (
                request_id,
                phone,
                purpose,
                _hash_password(code),
                request_ip,
                now,
                expires_at,
            ),
        )
        conn.commit()
    return {
        "request_id": request_id,
        "phone": phone,
        "code": code,
        "expires_in_seconds": settings["code_ttl_seconds"],
        "retry_after_seconds": settings["resend_interval_seconds"],
    }


def mark_sms_verification_sent(
    config: dict, request_id: str, provider_request_id: str | None
) -> None:
    db_path = app_mvp_db_path_from_config(config)
    ensure_db(db_path)
    with _connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT phone, purpose FROM sms_verification_requests
            WHERE id = ? AND status = 'pending'
            """,
            (request_id,),
        ).fetchone()
        if row is None:
            raise ValueError("验证码请求状态无效")
        conn.execute(
            """
            UPDATE sms_verification_requests
            SET status = 'superseded'
            WHERE phone = ? AND purpose = ? AND status = 'sent' AND id != ?
            """,
            (row["phone"], row["purpose"], request_id),
        )
        conn.execute(
            """
            UPDATE sms_verification_requests
            SET status = 'sent', provider_request_id = ?
            WHERE id = ?
            """,
            ((provider_request_id or "")[:128] or None, request_id),
        )
        conn.commit()


def mark_sms_verification_failed(config: dict, request_id: str) -> None:
    db_path = app_mvp_db_path_from_config(config)
    ensure_db(db_path)
    with _connect(db_path) as conn:
        conn.execute(
            """
            UPDATE sms_verification_requests
            SET status = 'failed'
            WHERE id = ? AND status = 'pending'
            """,
            (request_id,),
        )
        conn.commit()


def _consume_sms_verification(
    conn: sqlite3.Connection,
    phone: str,
    code: str,
    purpose: str,
) -> None:
    code = re.sub(r"\D", "", code or "")
    if len(code) != SMS_CODE_LENGTH:
        raise ValueError("验证码无效或已过期")
    if purpose not in SMS_PURPOSES:
        raise ValueError("验证码无效或已过期")
    now = now_iso()
    verification = conn.execute(
        """
        SELECT * FROM sms_verification_requests
        WHERE phone = ? AND purpose = ? AND status = 'sent'
        ORDER BY created_at DESC LIMIT 1
        """,
        (phone, purpose),
    ).fetchone()
    if (
        verification is None
        or verification["expires_at"] <= now
        or verification["attempts"] >= SMS_CODE_MAX_ATTEMPTS
    ):
        raise ValueError("验证码无效或已过期")
    if not _verify_password(code, verification["code_hash"]):
        attempts = verification["attempts"] + 1
        status = "locked" if attempts >= SMS_CODE_MAX_ATTEMPTS else "sent"
        conn.execute(
            """
            UPDATE sms_verification_requests SET attempts = ?, status = ?
            WHERE id = ?
            """,
            (attempts, status, verification["id"]),
        )
        conn.commit()
        raise ValueError("验证码无效或已过期")
    conn.execute(
        """
        UPDATE sms_verification_requests
        SET status = 'consumed', consumed_at = ?
        WHERE id = ?
        """,
        (now, verification["id"]),
    )


def verify_sms_code_and_login(
    config: dict,
    phone: str,
    code: str,
    nickname: str = "",
    purpose: str = SMS_PURPOSE_LOGIN,
) -> Dict[str, Any]:
    phone = validate_phone(phone)
    if purpose != SMS_PURPOSE_LOGIN:
        raise ValueError("验证码无效或已过期")

    db_path = app_mvp_db_path_from_config(config)
    ensure_db(db_path)
    now = now_iso()
    with _connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        _consume_sms_verification(conn, phone, code, purpose)
        sync_configured_admin_roles(config, conn)
        row = conn.execute("SELECT * FROM users WHERE phone = ?", (phone,)).fetchone()
        is_new_user = row is None
        if is_new_user:
            user_id = f"user_{uuid.uuid4().hex}"
            display_name = (nickname or "").strip() or mask_phone(phone)
            conn.execute(
                """
                INSERT INTO users(
                    id, nickname, login_type, invite_code, phone, password_hash,
                    password_updated_at, role, created_at, last_login_at
                ) VALUES (?, ?, 'phone_sms', '', ?, NULL, NULL, ?, ?, ?)
                """,
                (
                    user_id,
                    display_name,
                    phone,
                    role_for_phone(config, phone),
                    now,
                    now,
                ),
            )
        else:
            user_id = row["id"]
            conn.execute(
                """
                UPDATE users
                SET last_login_at = ?, login_type = 'phone_sms', role = ?
                WHERE id = ?
                """,
                (now, role_for_phone(config, phone), user_id),
            )
        _ensure_energy(conn, user_id, config)
        token, expires_at = _issue_token(conn, user_id, now)
        conn.commit()
        user = _row_to_user(
            conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        )
    return {
        "token": token,
        "expires_at": expires_at,
        "is_new_user": is_new_user,
        "user": user,
    }


def ensure_db(db_path: str) -> None:
    with _connect(db_path) as conn:
        _execute_schema(conn)
        _ensure_demo_seed(conn)


def _ensure_demo_seed(conn: sqlite3.Connection) -> None:
    created_at = now_iso()
    conn.execute(
        """
        INSERT OR IGNORE INTO users(id, nickname, login_type, invite_code, created_at, last_login_at)
        VALUES (?, 'Demo User', 'demo', ?, ?, ?)
        """,
        (DEMO_USER_ID, DEFAULT_INVITE_CODE, created_at, created_at),
    )
    conn.execute(
        "INSERT OR IGNORE INTO auth_tokens(token, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
        (DEMO_TOKEN, DEMO_USER_ID, created_at, (datetime.now(timezone.utc) + timedelta(days=3650)).isoformat()),
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO devices(
            id, device_code, display_name, online_status, firmware_version,
            current_version, latest_version, update_available, release_note
        ) VALUES (?, ?, '我的白泽', 'unknown', '0.1.0-demo', '0.1.0-demo', '0.1.0-demo', 0, '等待设备版本上报')
        """,
        (DEMO_DEVICE_ID, DEMO_DEVICE_CODE),
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO device_settings(device_id, baize_nickname, user_call_name, personality_mode, tts_voice)
        VALUES (?, '白泽', '小伙伴', 'curious', 'Sambert 知颖')
        """,
        (DEMO_DEVICE_ID,),
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO energy_accounts(user_id, current_energy, daily_limit, updated_at, last_recovered_on)
        VALUES (?, ?, ?, ?, ?)
        """,
        (DEMO_USER_ID, INITIAL_ENERGY, DAILY_ENERGY_LIMIT, created_at, today_key()),
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO tts_candidates(id, provider, voice, price_note, latency_note, score, is_default)
        VALUES ('aliyun-sambert-zhiying', '阿里云百炼', 'Sambert 知颖', '内测默认', '待实测', 8, 1)
        """
    )
    conn.commit()


def _row_to_user(row: sqlite3.Row) -> Dict[str, Any]:
    phone = row["phone"] if "phone" in row.keys() else None
    role = row["role"] if "role" in row.keys() and row["role"] in VALID_ROLES else USER_ROLE
    return {
        "id": row["id"],
        "nickname": row["nickname"],
        "display_name": row["nickname"],
        "phone": phone,
        "masked_phone": mask_phone(phone) if phone else None,
        "role": role,
        "login_type": row["login_type"],
        "invite_code": row["invite_code"],
        "created_at": row["created_at"],
        "last_login_at": row["last_login_at"],
        "has_password": bool(row["password_hash"])
        if "password_hash" in row.keys()
        else False,
    }


def _level_for_score(score: int) -> str:
    if score >= 700:
        return "默契"
    if score >= 300:
        return "亲近"
    if score >= 100:
        return "熟悉"
    return "初识"


def _progress_for_score(score: int) -> Dict[str, Any]:
    if score >= 700:
        return {"level": "默契", "score": score, "level_min": 700, "level_max": None, "progress": 1.0}
    if score >= 300:
        return {"level": "亲近", "score": score, "level_min": 300, "level_max": 700, "progress": (score - 300) / 400}
    if score >= 100:
        return {"level": "熟悉", "score": score, "level_min": 100, "level_max": 300, "progress": (score - 100) / 200}
    return {"level": "初识", "score": score, "level_min": 0, "level_max": 100, "progress": score / 100}


def _ensure_energy(conn: sqlite3.Connection, user_id: str, config: dict) -> sqlite3.Row:
    settings = spirit_power_settings(config)
    row = conn.execute("SELECT * FROM energy_accounts WHERE user_id = ?", (user_id,)).fetchone()
    today = product_day_key()
    now = now_iso()
    if row is None:
        conn.execute(
            """
            INSERT INTO energy_accounts(
                user_id, current_energy, daily_limit, updated_at,
                last_recovered_on, last_hourly_recovered_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user_id, settings["initial"], settings["max"], now, today, now),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM energy_accounts WHERE user_id = ?", (user_id,)).fetchone()
    elif row["daily_limit"] != settings["max"]:
        adjusted = min(
            settings["max"],
            max(0, row["current_energy"] + settings["max"] - row["daily_limit"]),
        )
        delta = adjusted - row["current_energy"]
        conn.execute(
            """
            UPDATE energy_accounts
            SET current_energy = ?, daily_limit = ?, updated_at = ?
            WHERE user_id = ?
            """,
            (adjusted, settings["max"], now, user_id),
        )
        if delta:
            conn.execute(
                "INSERT INTO energy_events(id, user_id, delta, reason, created_at) VALUES (?, ?, ?, 'spirit_power_config_adjustment', ?)",
                (f"energy_{uuid.uuid4().hex}", user_id, delta, now),
            )
        conn.commit()
        row = conn.execute("SELECT * FROM energy_accounts WHERE user_id = ?", (user_id,)).fetchone()

    if row["last_recovered_on"] != today:
        delta = max(0, row["daily_limit"] - row["current_energy"])
        conn.execute(
            """
            UPDATE energy_accounts
            SET current_energy = daily_limit, updated_at = ?, last_recovered_on = ?,
                last_hourly_recovered_at = ?
            WHERE user_id = ?
            """,
            (now, today, now, user_id),
        )
        if delta:
            conn.execute(
                "INSERT INTO energy_events(id, user_id, delta, reason, created_at) VALUES (?, ?, ?, 'daily_spirit_refill', ?)",
                (f"energy_{uuid.uuid4().hex}", user_id, delta, now),
            )
        conn.commit()
    else:
        last_hourly_raw = row["last_hourly_recovered_at"] or row["updated_at"] or now
        try:
            last_hourly = datetime.fromisoformat(last_hourly_raw.replace("Z", "+00:00"))
            current_time = datetime.fromisoformat(now.replace("Z", "+00:00"))
            elapsed_hours = max(0, int((current_time - last_hourly).total_seconds() // 3600))
        except (TypeError, ValueError):
            elapsed_hours = 0
            last_hourly = datetime.fromisoformat(now.replace("Z", "+00:00"))
        if elapsed_hours > 0:
            recovered = min(
                row["daily_limit"] - row["current_energy"],
                elapsed_hours * settings["recovery_per_hour"],
            )
            recovered_at = (last_hourly + timedelta(hours=elapsed_hours)).replace(microsecond=0).isoformat()
            conn.execute(
                """
                UPDATE energy_accounts
                SET current_energy = current_energy + ?, updated_at = ?, last_hourly_recovered_at = ?
                WHERE user_id = ?
                """,
                (max(0, recovered), now, recovered_at, user_id),
            )
            if recovered > 0:
                conn.execute(
                    "INSERT INTO energy_events(id, user_id, delta, reason, created_at) VALUES (?, ?, ?, 'hourly_spirit_recovery', ?)",
                    (f"energy_{uuid.uuid4().hex}", user_id, recovered, now),
                )
            conn.commit()
    return conn.execute("SELECT * FROM energy_accounts WHERE user_id = ?", (user_id,)).fetchone()


def _spirit_power_payload(conn: sqlite3.Connection, user_id: str, config: dict) -> Dict[str, Any]:
    settings = spirit_power_settings(config)
    row = _ensure_energy(conn, user_id, config)
    today = product_day_key()
    inventory_count = conn.execute(
        """
        SELECT COUNT(*) AS c FROM spirit_power_items
        WHERE user_id = ? AND used_at IS NULL AND expires_on >= ?
        """,
        (user_id, today),
    ).fetchone()["c"]
    checked_in = conn.execute(
        "SELECT 1 FROM spirit_power_checkins WHERE user_id = ? AND product_day = ?",
        (user_id, today),
    ).fetchone() is not None
    return {
        "current": row["current_energy"],
        "daily_limit": row["daily_limit"],
        "max": row["daily_limit"],
        "recovery_per_hour": settings["recovery_per_hour"],
        "conversation_cost": settings["conversation_cost"],
        "product_day_boundary_hour": PRODUCT_DAY_BOUNDARY_HOUR,
        "updated_at": row["updated_at"],
        "last_recovered_on": row["last_recovered_on"],
        "checked_in_today": checked_in,
        "spirit_dew_count": inventory_count,
        "spirit_dew_amount": settings["spirit_dew_amount"],
    }


def _energy_payload(conn: sqlite3.Connection, user_id: str, config: dict) -> Dict[str, Any]:
    return _spirit_power_payload(conn, user_id, config)


def consume_energy(config: dict, user_id: str, device_id: str | None, amount: int, reason: str) -> bool:
    db_path = app_mvp_db_path_from_config(config)
    ensure_db(db_path)
    with _connect(db_path) as conn:
        account = _ensure_energy(conn, user_id, config)
        if account["current_energy"] < amount:
            return False
        now = now_iso()
        conn.execute("UPDATE energy_accounts SET current_energy = current_energy - ?, updated_at = ? WHERE user_id = ?", (amount, now, user_id))
        conn.execute(
            "INSERT INTO energy_events(id, user_id, device_id, delta, reason, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (f"energy_{uuid.uuid4().hex}", user_id, device_id, -amount, reason, now),
        )
        conn.commit()
    from core.telemetry import energy_spent

    energy_spent(amount, reason)
    return True


def consume_spirit_power(config: dict, user_id: str, device_id: str | None, amount: int, reason: str) -> bool:
    return consume_energy(config, user_id, device_id, amount, reason)


def record_app_telemetry(
    config: dict,
    user_id: str,
    event_type: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    if event_type not in {"crash", "api"}:
        raise ValueError("event_type 不支持")
    platform = str(payload.get("platform", "unknown")).strip().lower()
    if platform not in {"android", "ios"}:
        raise ValueError("platform 不支持")
    item = {
        "id": f"telemetry_{uuid.uuid4().hex}",
        "user_id": user_id,
        "event_type": event_type,
        "platform": platform,
        "app_version": str(payload.get("app_version", ""))[:40] or None,
        "route": str(payload.get("route", ""))[:160] or None,
        "method": str(payload.get("method", "")).upper()[:12] or None,
        "status_code": int(payload["status_code"])
        if payload.get("status_code") is not None
        else None,
        "duration_ms": round(max(0.0, float(payload.get("duration_ms", 0))), 2),
        "error_type": str(payload.get("error_type", ""))[:120] or None,
        "message": str(payload.get("message", ""))[:1000] or None,
        "details_json": json.dumps(
            payload.get("details") if isinstance(payload.get("details"), dict) else {},
            ensure_ascii=False,
        )[:4000],
        "created_at": now_iso(),
    }
    db_path = app_mvp_db_path_from_config(config)
    ensure_db(db_path)
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO app_telemetry_events(
                id, user_id, event_type, platform, app_version, route, method,
                status_code, duration_ms, error_type, message, details_json,
                created_at
            ) VALUES (
                :id, :user_id, :event_type, :platform, :app_version, :route,
                :method, :status_code, :duration_ms, :error_type, :message,
                :details_json, :created_at
            )
            """,
            item,
        )
        conn.commit()
    return {"id": item["id"], "accepted": True}


def spirit_power_summary(config: dict, user_id: str) -> Dict[str, Any]:
    db_path = app_mvp_db_path_from_config(config)
    ensure_db(db_path)
    with _connect(db_path) as conn:
        return _spirit_power_payload(conn, user_id, config)


def _spirit_power_item_payload(row: sqlite3.Row) -> Dict[str, Any]:
    names = {"spirit_dew": "白泽灵露"}
    return {
        "id": row["id"],
        "item_type": row["item_type"],
        "name": names.get(row["item_type"], row["item_type"]),
        "amount": row["amount"],
        "expires_on": row["expires_on"],
        "granted_on": row["granted_on"],
    }


def list_spirit_power_items(config: dict, user_id: str) -> list[Dict[str, Any]]:
    db_path = app_mvp_db_path_from_config(config)
    ensure_db(db_path)
    with _connect(db_path) as conn:
        today = product_day_key()
        rows = conn.execute(
            """
            SELECT * FROM spirit_power_items
            WHERE user_id = ? AND used_at IS NULL AND expires_on >= ?
            ORDER BY expires_on, created_at
            """,
            (user_id, today),
        ).fetchall()
        return [_spirit_power_item_payload(row) for row in rows]


def check_in_spirit_power(config: dict, user_id: str) -> Dict[str, Any]:
    db_path = app_mvp_db_path_from_config(config)
    ensure_db(db_path)
    with _connect(db_path) as conn:
        settings = spirit_power_settings(config)
        _ensure_energy(conn, user_id, config)
        today = product_day_key()
        existing = conn.execute(
            "SELECT item_id FROM spirit_power_checkins WHERE user_id = ? AND product_day = ?",
            (user_id, today),
        ).fetchone()
        if existing:
            return {"already_checked_in": True, "spirit_power": _spirit_power_payload(conn, user_id, config)}
        inventory_count = conn.execute(
            """
            SELECT COUNT(*) AS c FROM spirit_power_items
            WHERE user_id = ? AND used_at IS NULL AND expires_on >= ?
            """,
            (user_id, today),
        ).fetchone()["c"]
        if inventory_count >= SPIRIT_DEW_MAX_INVENTORY:
            raise ValueError("白泽灵露已存满，请先使用后再签到")
        now = now_iso()
        item_id = f"spirit_item_{uuid.uuid4().hex}"
        expires_on = (
            datetime.fromisoformat(today).date() + timedelta(days=7)
        ).isoformat()
        conn.execute(
            """
            INSERT INTO spirit_power_items(
                id, user_id, item_type, amount, granted_on, expires_on, created_at
            ) VALUES (?, ?, 'spirit_dew', ?, ?, ?, ?)
            """,
            (item_id, user_id, settings["spirit_dew_amount"], today, expires_on, now),
        )
        conn.execute(
            "INSERT INTO spirit_power_checkins(user_id, product_day, item_id, created_at) VALUES (?, ?, ?, ?)",
            (user_id, today, item_id, now),
        )
        conn.execute(
            "INSERT INTO energy_events(id, user_id, delta, reason, created_at) VALUES (?, ?, 0, 'checkin_spirit_dew_grant', ?)",
            (f"energy_{uuid.uuid4().hex}", user_id, now),
        )
        conn.commit()
        return {
            "already_checked_in": False,
            "item": _spirit_power_item_payload(
                conn.execute("SELECT * FROM spirit_power_items WHERE id = ?", (item_id,)).fetchone()
            ),
            "spirit_power": _spirit_power_payload(conn, user_id, config),
        }


def use_spirit_dew(config: dict, user_id: str, item_id: str | None = None) -> Dict[str, Any]:
    db_path = app_mvp_db_path_from_config(config)
    ensure_db(db_path)
    with _connect(db_path) as conn:
        account = _ensure_energy(conn, user_id, config)
        today = product_day_key()
        if item_id:
            item = conn.execute(
                """
                SELECT * FROM spirit_power_items
                WHERE id = ? AND user_id = ? AND used_at IS NULL AND expires_on >= ?
                """,
                (item_id, user_id, today),
            ).fetchone()
        else:
            item = conn.execute(
                """
                SELECT * FROM spirit_power_items
                WHERE user_id = ? AND used_at IS NULL AND expires_on >= ?
                ORDER BY expires_on, created_at LIMIT 1
                """,
                (user_id, today),
            ).fetchone()
        if not item:
            raise ValueError("没有可用的白泽灵露")
        if account["current_energy"] > account["daily_limit"] - item["amount"]:
            raise ValueError(f"当前灵力高于 {account['daily_limit'] - item['amount']} 点，暂时不需要使用白泽灵露")
        now = now_iso()
        conn.execute("UPDATE spirit_power_items SET used_at = ? WHERE id = ?", (now, item["id"]))
        conn.execute(
            "UPDATE energy_accounts SET current_energy = current_energy + ?, updated_at = ? WHERE user_id = ?",
            (item["amount"], now, user_id),
        )
        conn.execute(
            "INSERT INTO energy_events(id, user_id, delta, reason, created_at) VALUES (?, ?, ?, 'spirit_dew_use', ?)",
            (f"energy_{uuid.uuid4().hex}", user_id, item["amount"], now),
        )
        conn.commit()
        return {"used_item_id": item["id"], "spirit_power": _spirit_power_payload(conn, user_id, config)}


def _ensure_intimacy(conn: sqlite3.Connection, user_id: str, device_id: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM intimacy_accounts WHERE user_id = ? AND device_id = ?",
        (user_id, device_id),
    ).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO intimacy_accounts(user_id, device_id, score, level, updated_at, streak_days) VALUES (?, ?, 0, '初识', ?, 0)",
            (user_id, device_id, now_iso()),
        )
        conn.commit()
    return conn.execute(
        "SELECT * FROM intimacy_accounts WHERE user_id = ? AND device_id = ?",
        (user_id, device_id),
    ).fetchone()


def intimacy_payload(conn: sqlite3.Connection, user_id: str, device_id: str | None = None) -> Dict[str, Any]:
    if device_id:
        row = conn.execute(
            "SELECT * FROM intimacy_accounts WHERE user_id = ? AND device_id = ?",
            (user_id, device_id),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM intimacy_accounts WHERE user_id = ? ORDER BY score DESC LIMIT 1",
            (user_id,),
        ).fetchone()
    if row is None:
        return _progress_for_score(0)
    payload = _progress_for_score(row["score"])
    payload.update(
        {
            "device_id": row["device_id"],
            "updated_at": row["updated_at"],
            "last_interaction_on": row["last_interaction_on"],
            "streak_days": row["streak_days"],
        }
    )
    event = conn.execute(
        "SELECT delta, reason, created_at FROM intimacy_events WHERE user_id = ? AND device_id = ? ORDER BY created_at DESC LIMIT 1",
        (user_id, row["device_id"]),
    ).fetchone()
    payload["recent_growth"] = dict(event) if event else None
    return payload


def add_intimacy(config: dict, user_id: str, device_id: str, delta: int, reason: str) -> Dict[str, Any]:
    db_path = app_mvp_db_path_from_config(config)
    ensure_db(db_path)
    with _connect(db_path) as conn:
        current = _ensure_intimacy(conn, user_id, device_id)
        score = max(0, current["score"] + delta)
        now = now_iso()
        conn.execute(
            "UPDATE intimacy_accounts SET score = ?, level = ?, updated_at = ? WHERE user_id = ? AND device_id = ?",
            (score, _level_for_score(score), now, user_id, device_id),
        )
        conn.execute(
            "INSERT INTO intimacy_events(id, user_id, device_id, delta, reason, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (f"intimacy_{uuid.uuid4().hex}", user_id, device_id, delta, reason, now),
        )
        conn.commit()
        return intimacy_payload(conn, user_id, device_id)


def record_dialogue_intimacy(config: dict, user_id: str, device_id: str) -> Dict[str, Any]:
    db_path = app_mvp_db_path_from_config(config)
    ensure_db(db_path)
    with _connect(db_path) as conn:
        current = _ensure_intimacy(conn, user_id, device_id)
        today = today_key()
        delta = 5 if current["last_interaction_on"] != today else 1
        reason = "first_dialogue_today" if delta == 5 else "valid_dialogue"
        score = max(0, current["score"] + delta)
        now = now_iso()
        conn.execute(
            "UPDATE intimacy_accounts SET score = ?, level = ?, updated_at = ?, last_interaction_on = ? WHERE user_id = ? AND device_id = ?",
            (score, _level_for_score(score), now, today, user_id, device_id),
        )
        conn.execute(
            "INSERT INTO intimacy_events(id, user_id, device_id, delta, reason, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (f"intimacy_{uuid.uuid4().hex}", user_id, device_id, delta, reason, now),
        )
        conn.commit()
        return intimacy_payload(conn, user_id, device_id)


def register_or_login_user(config: dict, invite_code: str, nickname: str) -> Dict[str, Any]:
    invite_code = (invite_code or "").strip()
    nickname = (nickname or "").strip()
    allowed_codes = config.get("app_mvp", {}).get("invite_codes") or [DEFAULT_INVITE_CODE]
    if invite_code not in allowed_codes:
        raise ValueError("内测码无效")
    if not nickname:
        raise ValueError("nickname 不能为空")
    db_path = app_mvp_db_path_from_config(config)
    ensure_db(db_path)
    now = now_iso()
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE invite_code = ? AND nickname = ?",
            (invite_code, nickname),
        ).fetchone()
        if row is None:
            user_id = f"user_{uuid.uuid4().hex}"
            conn.execute(
                "INSERT INTO users(id, nickname, login_type, invite_code, created_at, last_login_at) VALUES (?, ?, 'invite_code', ?, ?, ?)",
                (user_id, nickname, invite_code, now, now),
            )
        else:
            user_id = row["id"]
            conn.execute("UPDATE users SET last_login_at = ?, nickname = ? WHERE id = ?", (now, nickname, user_id))
        _ensure_energy(conn, user_id, config)
        token = f"mvp_{uuid.uuid4().hex}"
        expires_at = (datetime.now(timezone.utc) + timedelta(days=30)).replace(microsecond=0).isoformat()
        conn.execute(
            "INSERT INTO auth_tokens(token, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (token, user_id, now, expires_at),
        )
        conn.commit()
        user = _row_to_user(conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())
    return {"token": token, "expires_at": expires_at, "user": user}


def _issue_token(conn: sqlite3.Connection, user_id: str, now: str | None = None) -> tuple[str, str]:
    now = now or now_iso()
    token = f"mvp_{uuid.uuid4().hex}"
    expires_at = (datetime.now(timezone.utc) + timedelta(days=30)).replace(microsecond=0).isoformat()
    conn.execute(
        "INSERT INTO auth_tokens(token, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
        (token, user_id, now, expires_at),
    )
    return token, expires_at


def register_phone_user(
    config: dict,
    phone: str,
    password: str,
    nickname: str = "",
    verification_code: str | None = None,
    require_verification: bool = False,
) -> Dict[str, Any]:
    phone = validate_phone(phone)
    password = _validate_password(password)
    nickname = (nickname or "").strip() or mask_phone(phone)
    db_path = app_mvp_db_path_from_config(config)
    ensure_db(db_path)
    now = now_iso()
    role = role_for_phone(config, phone)
    with _connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        sync_configured_admin_roles(config, conn)
        exists = conn.execute("SELECT 1 FROM users WHERE phone = ?", (phone,)).fetchone()
        if exists:
            raise ValueError("phone 已注册")
        if require_verification:
            _consume_sms_verification(
                conn,
                phone,
                verification_code or "",
                SMS_PURPOSE_REGISTER,
            )
        user_id = f"user_{uuid.uuid4().hex}"
        conn.execute(
            """
            INSERT INTO users(
                id, nickname, login_type, invite_code, phone, password_hash,
                password_updated_at, role, created_at, last_login_at
            ) VALUES (?, ?, 'phone_password', '', ?, ?, ?, ?, ?, ?)
            """,
            (user_id, nickname, phone, _hash_password(password), now, role, now, now),
        )
        _ensure_energy(conn, user_id, config)
        token, expires_at = _issue_token(conn, user_id, now)
        conn.commit()
        user = _row_to_user(conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())
    return {"token": token, "expires_at": expires_at, "user": user}


def login_phone_user(config: dict, phone: str, password: str) -> Dict[str, Any]:
    phone = validate_login_phone(config, phone)
    password = _validate_password(password)
    db_path = app_mvp_db_path_from_config(config)
    ensure_db(db_path)
    now = now_iso()
    with _connect(db_path) as conn:
        sync_configured_admin_roles(config, conn)
        row = conn.execute("SELECT * FROM users WHERE phone = ?", (phone,)).fetchone()
        if row is None or not _verify_password(password, row["password_hash"]):
            raise ValueError("手机号或密码错误")
        conn.execute(
            "UPDATE users SET last_login_at = ?, role = ? WHERE id = ?",
            (now, role_for_phone(config, phone), row["id"]),
        )
        _ensure_energy(conn, row["id"], config)
        token, expires_at = _issue_token(conn, row["id"], now)
        conn.commit()
        user = _row_to_user(conn.execute("SELECT * FROM users WHERE id = ?", (row["id"],)).fetchone())
    return {"token": token, "expires_at": expires_at, "user": user}


def update_user_password(config: dict, user_id: str, old_password: str | None, new_password: str) -> bool:
    new_password = _validate_password(new_password)
    db_path = app_mvp_db_path_from_config(config)
    ensure_db(db_path)
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            return False
        if not row["phone"]:
            raise ValueError("当前账号不支持设置密码")
        if row["password_hash"] and not _verify_password(old_password or "", row["password_hash"]):
            return False
        now = now_iso()
        conn.execute(
            "UPDATE users SET password_hash = ?, password_updated_at = ?, login_type = 'phone_password' WHERE id = ?",
            (_hash_password(new_password), now, user_id),
        )
        conn.execute("DELETE FROM auth_tokens WHERE user_id = ?", (user_id,))
        conn.commit()
    return True


def reset_user_password_with_sms(
    config: dict,
    phone: str,
    code: str,
    new_password: str,
) -> bool:
    phone = validate_phone(phone)
    new_password = _validate_password(new_password)
    db_path = app_mvp_db_path_from_config(config)
    ensure_db(db_path)
    with _connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT id FROM users WHERE phone = ?", (phone,)).fetchone()
        if row is None:
            raise ValueError("验证码无效或已过期")
        _consume_sms_verification(
            conn,
            phone,
            code,
            SMS_PURPOSE_RESET_PASSWORD,
        )
        now = now_iso()
        conn.execute(
            """
            UPDATE users
            SET password_hash = ?, password_updated_at = ?, login_type = 'phone_password'
            WHERE id = ?
            """,
            (_hash_password(new_password), now, row["id"]),
        )
        conn.execute("DELETE FROM auth_tokens WHERE user_id = ?", (row["id"],))
        conn.commit()
    return True


def user_for_token(config: dict, token: str) -> Dict[str, Any] | None:
    if not token:
        return None
    db_path = app_mvp_db_path_from_config(config)
    ensure_db(db_path)
    with _connect(db_path) as conn:
        sync_configured_admin_roles(config, conn)
        row = conn.execute(
            """
            SELECT u.* FROM auth_tokens t
            JOIN users u ON u.id = t.user_id
            WHERE t.token = ? AND t.expires_at > ?
            """,
            (token, now_iso()),
        ).fetchone()
        return _row_to_user(row) if row else None


def _settings_payload(row: sqlite3.Row | None, device_id: str) -> Dict[str, Any]:
    if row is None:
        return {
            "device_id": device_id,
            "baize_nickname": "白泽",
            "user_call_name": "小伙伴",
            "personality_mode": "curious",
            "tts_voice": "Sambert 知颖",
            "speaker_volume": 100,
            "screen_brightness": 100,
        }
    return {
        "device_id": device_id,
        "baize_nickname": row["baize_nickname"],
        "user_call_name": row["user_call_name"],
        "personality_mode": row["personality_mode"],
        "tts_voice": row["tts_voice"],
        "speaker_volume": row["speaker_volume"] if row["speaker_volume"] is not None else 100,
        "screen_brightness": row["screen_brightness"] if row["screen_brightness"] is not None else 100,
    }


def device_payload(row: sqlite3.Row) -> Dict[str, Any]:
    payload = {
        "id": row["id"],
        "device_code": row["device_code"],
        "display_name": row["display_name"],
        "source_device_id": row["source_device_id"],
        "client_id": row["client_id"],
        "model": row["model"],
        "online_status": row["online_status"],
        "activity_status": row["activity_status"],
        "battery_percent": row["battery_percent"],
        "firmware_version": row["firmware_version"],
        "last_online_at": row["last_online_at"],
    }
    if "bound_at" in row.keys():
        payload["bound_at"] = row["bound_at"]
    return payload


def _is_bound_conn(conn: sqlite3.Connection, user_id: str, device_id: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM user_device_bindings WHERE user_id = ? AND device_id = ?",
        (user_id, device_id),
    ).fetchone() is not None


def bind_device(config: dict, user_id: str, device_code: str) -> Dict[str, Any] | None:
    db_path = app_mvp_db_path_from_config(config)
    ensure_db(db_path)
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM devices WHERE device_code = ?", (device_code,)).fetchone()
        if row is None:
            return None
        owner = conn.execute("SELECT user_id FROM user_device_bindings WHERE device_id = ?", (row["id"],)).fetchone()
        if owner is not None and owner["user_id"] != user_id:
            raise ValueError("device already bound")
        conn.execute(
            "INSERT OR IGNORE INTO user_device_bindings(user_id, device_id, bound_at) VALUES (?, ?, ?)",
            (user_id, row["id"], now_iso()),
        )
        from core.api.app_memory_store import activate_relationship_conn

        activate_relationship_conn(conn, user_id, row["id"])
        _ensure_intimacy(conn, user_id, row["id"])
        conn.commit()
        bound_at = conn.execute(
            "SELECT bound_at FROM user_device_bindings WHERE user_id = ? AND device_id = ?",
            (user_id, row["id"]),
        ).fetchone()["bound_at"]
        payload = device_payload(row)
        payload["bound_at"] = bound_at
        return payload


def create_device(
    config: dict,
    device_code: str | None = None,
    display_name: str | None = None,
    source_device_id: str | None = None,
    client_id: str | None = None,
    model: str | None = None,
    firmware_version: str | None = None,
) -> Dict[str, Any]:
    db_path = app_mvp_db_path_from_config(config)
    ensure_db(db_path)
    with _connect(db_path) as conn:
        row = _insert_device(
            conn,
            device_code=device_code,
            display_name=display_name,
            source_device_id=source_device_id,
            client_id=client_id,
            model=model,
            firmware_version=firmware_version,
        )
        conn.commit()
        return device_payload(row)


def list_admin_devices(config: dict) -> list[Dict[str, Any]]:
    db_path = app_mvp_db_path_from_config(config)
    ensure_db(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT d.*, b.user_id AS bound_user_id, b.bound_at
            FROM devices d
            LEFT JOIN user_device_bindings b ON b.device_id = d.id
            ORDER BY d.id
            """
        ).fetchall()
        items = []
        for row in rows:
            item = device_payload(row)
            item["bound_user_id"] = row["bound_user_id"]
            item["bound_at"] = row["bound_at"]
            items.append(item)
        return items


def list_admin_users(config: dict) -> list[Dict[str, Any]]:
    """Return a privacy-minimized user overview for the lightweight dashboard."""
    db_path = app_mvp_db_path_from_config(config)
    ensure_db(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT u.id, u.nickname, u.phone, u.login_type, u.role,
                   u.password_hash, u.created_at, u.last_login_at,
                   COUNT(DISTINCT b.device_id) AS device_count,
                   GROUP_CONCAT(DISTINCT d.display_name) AS device_names
            FROM users u
            LEFT JOIN user_device_bindings b ON b.user_id = u.id
            LEFT JOIN devices d ON d.id = b.device_id
            GROUP BY u.id
            ORDER BY u.last_login_at DESC, u.created_at DESC
            """
        ).fetchall()
        return [
            {
                "id": row["id"],
                "nickname": row["nickname"],
                "masked_phone": mask_phone(row["phone"]) if row["phone"] else None,
                "login_type": row["login_type"],
                "role": row["role"],
                "has_password": bool(row["password_hash"]),
                "created_at": row["created_at"],
                "last_login_at": row["last_login_at"],
                "device_count": row["device_count"],
                "device_names": row["device_names"].split(",") if row["device_names"] else [],
            }
            for row in rows
        ]


def set_dashboard_admin(config: dict, username: str, password: str) -> None:
    username = (username or "").strip()
    if not re.fullmatch(r"[a-zA-Z0-9_-]{4,40}", username):
        raise ValueError("运营账号格式不正确")
    if len(password or "") < 12:
        raise ValueError("运营密码至少需要 12 位")
    db_path = app_mvp_db_path_from_config(config)
    ensure_db(db_path)
    now = now_iso()
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO dashboard_admins(username, password_hash, created_at, last_login_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(username) DO UPDATE SET password_hash = excluded.password_hash
            """,
            (username, _hash_password(password), now, now),
        )
        conn.execute("DELETE FROM dashboard_tokens WHERE username = ?", (username,))
        conn.commit()


def login_dashboard_admin(config: dict, username: str, password: str) -> Dict[str, str]:
    username = (username or "").strip()
    db_path = app_mvp_db_path_from_config(config)
    ensure_db(db_path)
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(hours=8)
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT password_hash FROM dashboard_admins WHERE username = ?", (username,)
        ).fetchone()
        if row is None or not _verify_password(password, row["password_hash"]):
            raise ValueError("运营账号或密码错误")
        token = f"ops_{secrets.token_urlsafe(32)}"
        conn.execute("DELETE FROM dashboard_tokens WHERE expires_at <= ?", (now.isoformat(),))
        conn.execute(
            "INSERT INTO dashboard_tokens(token, username, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (token, username, now.isoformat(), expires_at.isoformat()),
        )
        conn.execute(
            "UPDATE dashboard_admins SET last_login_at = ? WHERE username = ?",
            (now.isoformat(), username),
        )
        conn.commit()
    return {"token": token, "expires_at": expires_at.isoformat()}


def dashboard_admin_for_token(config: dict, token: str) -> str | None:
    if not token or not token.startswith("ops_"):
        return None
    db_path = app_mvp_db_path_from_config(config)
    ensure_db(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT username, expires_at FROM dashboard_tokens WHERE token = ?", (token,)
        ).fetchone()
        if row is None or datetime.fromisoformat(row["expires_at"]) <= datetime.now(timezone.utc):
            return None
        return row["username"]


SUPPORT_TICKET_STATUSES = {"open", "in_progress", "resolved", "closed"}
SUPPORT_TICKET_CATEGORIES = {"device", "account", "network", "ota", "content", "other"}


def _support_ticket_payload(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "device_id": row["device_id"],
        "category": row["category"],
        "subject": row["subject"],
        "message": row["message"],
        "status": row["status"],
        "operator_reply": row["operator_reply"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "resolved_at": row["resolved_at"],
    }


def create_support_ticket(
    config: dict,
    user_id: str,
    category: str,
    subject: str,
    message: str,
    device_id: str | None = None,
) -> Dict[str, Any]:
    category = (category or "other").strip().lower()
    subject = (subject or "").strip()
    message = (message or "").strip()
    device_id = (device_id or "").strip() or None
    if category not in SUPPORT_TICKET_CATEGORIES:
        raise ValueError("问题类型不支持")
    if not 2 <= len(subject) <= 80:
        raise ValueError("问题标题需要 2 到 80 个字")
    if not 5 <= len(message) <= 2000:
        raise ValueError("问题描述需要 5 到 2000 个字")
    db_path = app_mvp_db_path_from_config(config)
    ensure_db(db_path)
    now = datetime.now(timezone.utc)
    with _connect(db_path) as conn:
        if device_id and not _is_bound_conn(conn, user_id, device_id):
            raise ValueError("设备不存在或未绑定")
        recent_count = conn.execute(
            "SELECT COUNT(*) AS c FROM support_tickets WHERE user_id = ? AND created_at >= ?",
            (user_id, (now - timedelta(hours=1)).isoformat()),
        ).fetchone()["c"]
        if recent_count >= 5:
            raise ValueError("提交过于频繁，请一小时后再试")
        item = {
            "id": f"ticket_{uuid.uuid4().hex}",
            "user_id": user_id,
            "device_id": device_id,
            "category": category,
            "subject": subject,
            "message": message,
            "status": "open",
            "operator_reply": None,
            "operator_username": None,
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "resolved_at": None,
        }
        conn.execute(
            """
            INSERT INTO support_tickets(
                id, user_id, device_id, category, subject, message, status,
                operator_reply, operator_username, created_at, updated_at, resolved_at
            ) VALUES (
                :id, :user_id, :device_id, :category, :subject, :message, :status,
                :operator_reply, :operator_username, :created_at, :updated_at, :resolved_at
            )
            """,
            item,
        )
        conn.commit()
        return _support_ticket_payload(conn.execute(
            "SELECT * FROM support_tickets WHERE id = ?", (item["id"],)
        ).fetchone())


def list_user_support_tickets(config: dict, user_id: str) -> list[Dict[str, Any]]:
    db_path = app_mvp_db_path_from_config(config)
    ensure_db(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM support_tickets WHERE user_id = ? ORDER BY created_at DESC LIMIT 100",
            (user_id,),
        ).fetchall()
        return [_support_ticket_payload(row) for row in rows]


def list_ops_support_tickets(config: dict, status: str | None = None) -> list[Dict[str, Any]]:
    status = (status or "").strip().lower() or None
    if status and status not in SUPPORT_TICKET_STATUSES:
        raise ValueError("工单状态不支持")
    db_path = app_mvp_db_path_from_config(config)
    ensure_db(db_path)
    with _connect(db_path) as conn:
        where = "WHERE t.status = ?" if status else ""
        values = (status,) if status else ()
        rows = conn.execute(
            f"""
            SELECT t.*, u.nickname, u.phone, d.display_name AS device_name
            FROM support_tickets t
            JOIN users u ON u.id = t.user_id
            LEFT JOIN devices d ON d.id = t.device_id
            {where}
            ORDER BY CASE t.status WHEN 'open' THEN 0 WHEN 'in_progress' THEN 1 ELSE 2 END,
                     t.updated_at DESC LIMIT 300
            """,
            values,
        ).fetchall()
        items = []
        for row in rows:
            item = _support_ticket_payload(row)
            item.update({
                "nickname": row["nickname"],
                "masked_phone": mask_phone(row["phone"]) if row["phone"] else None,
                "device_name": row["device_name"],
            })
            items.append(item)
        return items


def update_support_ticket(
    config: dict,
    ticket_id: str,
    status: str,
    operator_reply: str,
    operator_username: str,
) -> Dict[str, Any] | None:
    status = (status or "").strip().lower()
    reply = (operator_reply or "").strip()
    if status not in SUPPORT_TICKET_STATUSES:
        raise ValueError("工单状态不支持")
    if len(reply) > 2000:
        raise ValueError("回复不能超过 2000 个字")
    now = now_iso()
    resolved_at = now if status in {"resolved", "closed"} else None
    db_path = app_mvp_db_path_from_config(config)
    ensure_db(db_path)
    with _connect(db_path) as conn:
        if conn.execute("SELECT 1 FROM support_tickets WHERE id = ?", (ticket_id,)).fetchone() is None:
            return None
        conn.execute(
            """
            UPDATE support_tickets
            SET status = ?, operator_reply = ?, operator_username = ?,
                updated_at = ?, resolved_at = ?
            WHERE id = ?
            """,
            (status, reply or None, operator_username, now, resolved_at, ticket_id),
        )
        conn.commit()
        return _support_ticket_payload(conn.execute(
            "SELECT * FROM support_tickets WHERE id = ?", (ticket_id,)
        ).fetchone())


def export_user_data(config: dict, user_id: str) -> Dict[str, Any]:
    """Return a privacy-scoped machine-readable export for the current account."""
    db_path = app_mvp_db_path_from_config(config)
    ensure_db(db_path)
    with _connect(db_path) as conn:
        user = conn.execute(
            "SELECT id, nickname, phone, login_type, created_at, last_login_at FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        if user is None:
            return {}
        devices = [dict(row) for row in conn.execute(
            """
            SELECT d.id, d.display_name, d.model, d.firmware_version, d.last_online_at, b.bound_at
            FROM user_device_bindings b JOIN devices d ON d.id = b.device_id
            WHERE b.user_id = ? ORDER BY b.bound_at
            """,
            (user_id,),
        ).fetchall()]
        settings = [dict(row) for row in conn.execute(
            """
            SELECT s.* FROM device_settings s JOIN user_device_bindings b ON b.device_id = s.device_id
            WHERE b.user_id = ?
            """,
            (user_id,),
        ).fetchall()]
        dialogues = [dict(row) for row in conn.execute(
            "SELECT id, device_id, user_text, baize_text, emotion, created_at FROM dialogues WHERE user_id = ? ORDER BY created_at",
            (user_id,),
        ).fetchall()]
        diaries = []
        for row in conn.execute(
            "SELECT id, device_id, date, title, summary, primary_emotion, dialogue_count, baize_note, generated_at FROM diaries WHERE user_id = ? ORDER BY date",
            (user_id,),
        ).fetchall():
            diaries.append(dict(row))
        memories = [dict(row) for row in conn.execute(
            "SELECT id, device_id, type, content, created_at, updated_at, disabled_at FROM memory_items WHERE user_id = ? ORDER BY created_at",
            (user_id,),
        ).fetchall()]
        tickets = [_support_ticket_payload(row) for row in conn.execute(
            "SELECT * FROM support_tickets WHERE user_id = ? ORDER BY created_at", (user_id,)
        ).fetchall()]
    account = dict(user)
    account["phone"] = mask_phone(account["phone"]) if account.get("phone") else None
    return {
        "exported_at": now_iso(),
        "account": account,
        "devices": devices,
        "device_settings": settings,
        "dialogues": dialogues,
        "diaries": diaries,
        "memories": memories,
        "support_tickets": tickets,
    }


def operations_snapshot(config: dict) -> Dict[str, Any]:
    db_path = app_mvp_db_path_from_config(config)
    ensure_db(db_path)
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    with _connect(db_path) as conn:
        devices = list_admin_devices(config)
        versions = {
            row["version"] or "unknown": row["c"]
            for row in conn.execute(
                "SELECT COALESCE(NULLIF(firmware_version, ''), current_version) AS version, COUNT(*) AS c FROM devices GROUP BY version"
            ).fetchall()
        }
        return {
            "devices": devices,
            "firmware_versions": versions,
            "open_tickets": conn.execute(
                "SELECT COUNT(*) AS c FROM support_tickets WHERE status IN ('open', 'in_progress')"
            ).fetchone()["c"],
            "app_crashes_24h": conn.execute(
                "SELECT COUNT(*) AS c FROM app_telemetry_events WHERE event_type = 'crash' AND created_at >= ?",
                (cutoff,),
            ).fetchone()["c"],
            "app_api_5xx_24h": conn.execute(
                "SELECT COUNT(*) AS c FROM app_telemetry_events WHERE event_type = 'api' AND status_code >= 500 AND created_at >= ?",
                (cutoff,),
            ).fetchone()["c"],
        }


def delete_user_account(config: dict, user_id: str) -> bool:
    """Permanently remove a non-demo account and its user-owned data."""
    if user_id == DEMO_USER_ID:
        raise ValueError("Demo 公共账号不能注销")
    db_path = app_mvp_db_path_from_config(config)
    ensure_db(db_path)
    with _connect(db_path) as conn:
        if conn.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone() is None:
            return False
        device_ids = [
            row["device_id"]
            for row in conn.execute(
                "SELECT device_id FROM user_device_bindings WHERE user_id = ?", (user_id,)
            ).fetchall()
        ]
        memory_ids = [
            row["id"]
            for row in conn.execute(
                "SELECT id FROM memory_items WHERE user_id = ?", (user_id,)
            ).fetchall()
        ]
        if memory_ids:
            placeholders = ",".join("?" for _ in memory_ids)
            conn.execute(f"DELETE FROM memory_versions WHERE memory_id IN ({placeholders})", memory_ids)
        for table in (
            "support_tickets",
            "memory_usage_events",
            "memory_jobs",
            "memory_items",
            "pet_growth_events",
            "device_relationships",
            "app_telemetry_events",
            "emotion_stats",
            "intimacy_events",
            "intimacy_accounts",
            "spirit_power_checkins",
            "spirit_power_items",
            "energy_events",
            "energy_accounts",
            "memories",
            "diaries",
            "dialogues",
            "user_device_bindings",
            "auth_tokens",
        ):
            conn.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        for device_id in device_ids:
            conn.execute(
                """
                UPDATE device_settings
                SET baize_nickname = '白泽', user_call_name = '小主人', personality_mode = 'warm'
                WHERE device_id = ?
                  AND NOT EXISTS (
                      SELECT 1 FROM user_device_bindings WHERE device_id = ?
                  )
                """,
                (device_id, device_id),
            )
        conn.commit()
        return True


def rotate_device_code(config: dict, device_id: str, device_code: str | None = None) -> Dict[str, Any] | None:
    new_code = (device_code or str(uuid.uuid4().int)[0:6]).strip()
    if not new_code:
        raise ValueError("device_code 不能为空")
    db_path = app_mvp_db_path_from_config(config)
    ensure_db(db_path)
    with _connect(db_path) as conn:
        if conn.execute("SELECT 1 FROM devices WHERE device_code = ? AND id != ?", (new_code, device_id)).fetchone():
            raise ValueError("device_code 已存在")
        row = conn.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()
        if row is None:
            return None
        conn.execute("UPDATE devices SET device_code = ? WHERE id = ?", (new_code, device_id))
        conn.commit()
        return device_payload(conn.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone())


def bound_device(config: dict, user_id: str, device_id: str) -> Dict[str, Any] | None:
    db_path = app_mvp_db_path_from_config(config)
    ensure_db(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT d.*, b.bound_at FROM devices d
            JOIN user_device_bindings b ON b.device_id = d.id
            WHERE b.user_id = ? AND d.id = ?
            """,
            (user_id, device_id),
        ).fetchone()
        return device_payload(row) if row else None


def list_devices(config: dict, user_id: str) -> list[Dict[str, Any]]:
    db_path = app_mvp_db_path_from_config(config)
    ensure_db(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT d.*, b.bound_at FROM devices d
            JOIN user_device_bindings b ON b.device_id = d.id
            WHERE b.user_id = ?
            ORDER BY b.bound_at DESC
            """,
            (user_id,),
        ).fetchall()
        return [device_payload(row) for row in rows]


def update_device_name(config: dict, user_id: str, device_id: str, display_name: str) -> Dict[str, Any] | None:
    db_path = app_mvp_db_path_from_config(config)
    ensure_db(db_path)
    with _connect(db_path) as conn:
        if not _is_bound_conn(conn, user_id, device_id):
            return None
        conn.execute("UPDATE devices SET display_name = ? WHERE id = ?", (display_name, device_id))
        conn.commit()
        return device_payload(conn.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone())


def unbind_device(config: dict, user_id: str, device_id: str) -> bool:
    db_path = app_mvp_db_path_from_config(config)
    ensure_db(db_path)
    with _connect(db_path) as conn:
        from core.api.app_memory_store import archive_relationship_conn

        if not _is_bound_conn(conn, user_id, device_id):
            return False
        archive_relationship_conn(conn, user_id, device_id)
        cur = conn.execute(
            "DELETE FROM user_device_bindings WHERE user_id = ? AND device_id = ?",
            (user_id, device_id),
        )
        if cur.rowcount:
            conn.execute(
                "UPDATE device_settings SET user_call_name = '小伙伴' WHERE device_id = ?",
                (device_id,),
            )
        conn.commit()
        return cur.rowcount > 0


def get_settings(config: dict, user_id: str, device_id: str) -> Dict[str, Any] | None:
    db_path = app_mvp_db_path_from_config(config)
    ensure_db(db_path)
    with _connect(db_path) as conn:
        if not _is_bound_conn(conn, user_id, device_id):
            return None
        return _settings_payload(
            conn.execute("SELECT * FROM device_settings WHERE device_id = ?", (device_id,)).fetchone(),
            device_id,
        )


def update_settings(config: dict, user_id: str, device_id: str, values: Dict[str, Any]) -> Dict[str, Any] | None:
    db_path = app_mvp_db_path_from_config(config)
    ensure_db(db_path)
    with _connect(db_path) as conn:
        if not _is_bound_conn(conn, user_id, device_id):
            return None
        row = conn.execute(
            "SELECT * FROM device_settings WHERE device_id = ?", (device_id,)
        ).fetchone()
        current = _settings_payload(row, device_id)
        for key in ("baize_nickname", "user_call_name", "personality_mode", "tts_voice"):
            if values.get(key) is not None:
                current[key] = values[key]
        stored_hardware = {
            key: row[key] if row is not None else None
            for key in ("speaker_volume", "screen_brightness")
        }
        for key in stored_hardware:
            if values.get(key) is not None:
                stored_hardware[key] = values[key]
                current[key] = values[key]
        conn.execute(
            """
            INSERT INTO device_settings(
                device_id, baize_nickname, user_call_name, personality_mode, tts_voice,
                speaker_volume, screen_brightness
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(device_id) DO UPDATE SET
                baize_nickname = excluded.baize_nickname,
                user_call_name = excluded.user_call_name,
                personality_mode = excluded.personality_mode,
                tts_voice = excluded.tts_voice,
                speaker_volume = excluded.speaker_volume,
                screen_brightness = excluded.screen_brightness
            """,
            (
                device_id,
                current["baize_nickname"],
                current["user_call_name"],
                current["personality_mode"],
                current["tts_voice"],
                stored_hardware["speaker_volume"],
                stored_hardware["screen_brightness"],
            ),
        )
        conn.commit()
        return current


def configured_hardware_settings(config: dict, device_id: str) -> Dict[str, int]:
    """Return only values explicitly saved by the App, for connection-time replay."""
    db_path = app_mvp_db_path_from_config(config)
    ensure_db(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT speaker_volume, screen_brightness FROM device_settings WHERE device_id = ?",
            (device_id,),
        ).fetchone()
        if row is None:
            return {}
        return {
            key: int(row[key])
            for key in ("speaker_volume", "screen_brightness")
            if row[key] is not None
        }


def _date_from_iso(value: str) -> str:
    try:
        return diary_date_key(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except Exception:
        return diary_date_key()


def _latest_bound_user_for_device(conn: sqlite3.Connection, device_id: str) -> str:
    row = conn.execute(
        """
        SELECT user_id
        FROM user_device_bindings
        WHERE device_id = ?
        ORDER BY bound_at DESC, rowid DESC
        LIMIT 1
        """,
        (device_id,),
    ).fetchone()
    return row["user_id"] if row else DEMO_USER_ID


def _device_id_for_source(conn: sqlite3.Connection, source_device_id: str) -> str:
    if source_device_id:
        row = conn.execute(
            "SELECT id FROM devices WHERE source_device_id = ? OR client_id = ?",
            (source_device_id, source_device_id),
        ).fetchone()
        if row:
            return row["id"]
    return DEMO_DEVICE_ID


def _find_device_row_for_source(
    conn: sqlite3.Connection,
    source_device_id: str = "",
    client_id: str = "",
) -> sqlite3.Row | None:
    source_device_id = (source_device_id or "").strip()
    client_id = (client_id or "").strip()
    if not source_device_id and not client_id:
        return None
    identities = [value for value in (source_device_id, client_id) if value]
    placeholders = ",".join("?" for _ in identities)
    return conn.execute(
        f"""
        SELECT * FROM devices
        WHERE source_device_id IN ({placeholders}) OR client_id IN ({placeholders})
        ORDER BY id LIMIT 1
        """,
        (*identities, *identities),
    ).fetchone()


def _generate_unique_device_code(conn: sqlite3.Connection) -> str:
    for _ in range(20):
        device_code = f"{uuid.uuid4().int % 1000000:06d}"
        if not conn.execute("SELECT 1 FROM devices WHERE device_code = ?", (device_code,)).fetchone():
            return device_code
    raise ValueError("无法生成唯一设备码")


def _claimable_seed_device_row(conn: sqlite3.Connection) -> sqlite3.Row | None:
    row = conn.execute(
        """
        SELECT d.* FROM devices d
        JOIN user_device_bindings b ON b.device_id = d.id
        WHERE d.id = ?
          AND COALESCE(d.source_device_id, '') = ''
          AND COALESCE(d.client_id, '') = ''
        LIMIT 1
        """,
        (DEMO_DEVICE_ID,),
    ).fetchone()
    return row


def _insert_device(
    conn: sqlite3.Connection,
    device_code: str | None = None,
    display_name: str | None = None,
    source_device_id: str | None = None,
    client_id: str | None = None,
    model: str | None = None,
    firmware_version: str | None = None,
    online_status: str = "unknown",
    activity_status: str = "unknown",
) -> sqlite3.Row:
    clean_code = (device_code or "").strip() or _generate_unique_device_code(conn)
    if conn.execute("SELECT 1 FROM devices WHERE device_code = ?", (clean_code,)).fetchone():
        raise ValueError("device_code 已存在")

    clean_display_name = (display_name or "我的白泽").strip() or "我的白泽"
    clean_version = (firmware_version or "0.1.0-demo").strip() or "0.1.0-demo"
    device_id = f"baize_{uuid.uuid4().hex[:12]}"
    conn.execute(
        """
        INSERT INTO devices(
            id, device_code, display_name, source_device_id, client_id, model,
            online_status, activity_status, firmware_version, current_version, latest_version,
            update_available, release_note
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
        """,
        (
            device_id,
            clean_code,
            clean_display_name,
            (source_device_id or "").strip() or None,
            (client_id or "").strip() or None,
            (model or "").strip() or None,
            online_status,
            activity_status,
            clean_version,
            clean_version,
            clean_version,
            f"设备当前版本 {clean_version}",
        ),
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO device_settings(device_id, baize_nickname, user_call_name, personality_mode, tts_voice)
        VALUES (?, '白泽', '小伙伴', 'curious', 'Sambert 知颖')
        """,
        (device_id,),
    )
    return conn.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()


def resolve_bound_app_device(
    config: dict,
    source_device_id: str = "",
    client_id: str = "",
    device_id: str = "",
) -> Dict[str, Any] | None:
    db_path = app_mvp_db_path_from_config(config)
    ensure_db(db_path)
    source_device_id = source_device_id or ""
    client_id = client_id or ""
    device_id = device_id or ""
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT d.*, b.user_id
            FROM devices d
            JOIN user_device_bindings b ON b.device_id = d.id
            WHERE d.id = ? OR d.source_device_id = ? OR d.client_id = ? OR d.source_device_id = ? OR d.client_id = ?
            ORDER BY b.bound_at DESC, b.rowid DESC LIMIT 1
            """,
            (device_id, source_device_id, source_device_id, client_id, client_id),
        ).fetchone()
        if not row:
            return None
        payload = device_payload(row)
        payload["user_id"] = row["user_id"]
        return payload


def append_dialogue(
    config: dict,
    source_device_id: str,
    session_id: str,
    user_text: str,
    baize_text: str,
    emotion: str = "neutral",
    user_id: str | None = None,
    device_id: str | None = None,
    source: str = "voice",
    memory_opt_out: bool = False,
    memory_retrieval: dict | None = None,
    user_audio_seconds: float = 0.0,
    spirit_power_cost: int = 0,
) -> Dict[str, Any]:
    user_text = (user_text or "").strip()
    inferred_emotion = infer_emotion(baize_text, emotion or "neutral")
    baize_text = clean_baize_text(baize_text)
    if not user_text or not baize_text:
        return {}
    from core.content_safety import evaluate_text

    if not evaluate_text(config, user_text, direction="input").allowed:
        return {}
    if not evaluate_text(config, baize_text, direction="output").allowed:
        return {}

    db_path = app_mvp_db_path_from_config(config)
    ensure_db(db_path)
    with _connect(db_path) as conn:
        device_id = device_id or _device_id_for_source(conn, source_device_id)
        user_id = user_id or _latest_bound_user_for_device(conn, device_id)
        created_at = now_iso()
        item = {
            "id": f"dlg_{uuid.uuid4().hex}",
            "user_id": user_id,
            "device_id": device_id,
            "source_device_id": source_device_id,
            "session_id": session_id,
            "user_text": user_text,
            "baize_text": baize_text,
            "emotion": inferred_emotion,
            "user_audio_seconds": round(max(0.0, float(user_audio_seconds)), 3),
            "spirit_power_cost": max(0, int(spirit_power_cost)),
            "created_at": created_at,
        }
        conn.execute(
            """
            INSERT INTO dialogues(
                id, user_id, device_id, source_device_id, session_id,
                user_text, baize_text, emotion, user_audio_seconds,
                spirit_power_cost, created_at
            ) VALUES (
                :id, :user_id, :device_id, :source_device_id, :session_id,
                :user_text, :baize_text, :emotion, :user_audio_seconds,
                :spirit_power_cost, :created_at
            )
            """,
            item,
        )
        conn.execute(
            "UPDATE devices SET online_status = 'online', last_online_at = ? WHERE id = ?",
            (created_at, device_id),
        )
        conn.execute(
            "INSERT INTO emotion_stats(id, user_id, device_id, emotion, source, created_at) VALUES (?, ?, ?, ?, 'dialogue', ?)",
            (f"emotion_{uuid.uuid4().hex}", user_id, device_id, inferred_emotion, created_at),
        )
        if user_id and device_id:
            from core.api.app_memory_store import record_dialogue_memory_conn

            record_dialogue_memory_conn(
                conn,
                config,
                user_id=user_id,
                device_id=device_id,
                dialogue_id=item["id"],
                user_text=user_text,
                opt_out=memory_opt_out,
            )
        conn.commit()
    record_dialogue_intimacy(config, user_id, device_id)
    if user_id and device_id and memory_retrieval:
        from core.api.app_memory_store import mark_memory_context_used

        mark_memory_context_used(
            config, user_id, device_id, item["id"], memory_retrieval
        )
    from core.telemetry import dialogue_persisted

    dialogue_persisted(source, inferred_emotion)
    return item


def list_dialogues(config: dict, user_id: str, device_id: str) -> list[Dict[str, Any]] | None:
    db_path = app_mvp_db_path_from_config(config)
    ensure_db(db_path)
    with _connect(db_path) as conn:
        if not _is_bound_conn(conn, user_id, device_id):
            return None
        rows = conn.execute(
            """
            SELECT * FROM dialogues
            WHERE user_id = ? AND device_id = ?
            ORDER BY created_at DESC LIMIT 100
            """,
            (user_id, device_id),
        ).fetchall()
        return [dict(row) for row in rows if not is_legacy_xiaozhi_dialogue(dict(row))]


def _dialogues_for_date(
    conn: sqlite3.Connection,
    user_id: str,
    device_id: str,
    diary_date: str,
    shared_device_dialogues: bool = False,
) -> list[Dict[str, Any]]:
    if shared_device_dialogues:
        rows = conn.execute(
            """
            SELECT * FROM dialogues
            WHERE device_id = ?
            ORDER BY created_at ASC
            """,
            (device_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT * FROM dialogues
            WHERE user_id = ? AND device_id = ?
            ORDER BY created_at ASC
            """,
            (user_id, device_id),
        ).fetchall()
    return [dict(row) for row in rows if _date_from_iso(row["created_at"]) == diary_date]


def _primary_emotion(dialogues: list[Dict[str, Any]]) -> str:
    for item in reversed(dialogues):
        emotion = normalize_emotion(str(item.get("emotion") or ""))
        if emotion and emotion != "neutral":
            return emotion
    return normalize_emotion(dialogues[-1].get("emotion") if dialogues else "neutral")


def _diary_payload(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "date": row["date"],
        "title": row["title"],
        "summary": row["summary"],
        "primary_emotion": row["primary_emotion"],
        "dialogue_count": row["dialogue_count"],
        "quotes": json.loads(row["quotes_json"] or "[]"),
        "baize_note": row["baize_note"],
        "generated_at": row["generated_at"],
    }


def _compact_text(text: str, max_length: int = 42) -> str:
    compacted = re.sub(r"\s+", " ", (text or "").strip())
    if len(compacted) <= max_length:
        return compacted
    return f"{compacted[:max_length].rstrip()}..."


def _local_time_word(item: Dict[str, Any]) -> str:
    value = str(item.get("created_at", ""))
    try:
        created_at = datetime.fromisoformat(value.replace("Z", "+00:00"))
        local_hour = (created_at.astimezone(timezone.utc) + timedelta(hours=8)).hour
    except Exception:
        local_hour = 20
    if 5 <= local_hour < 11:
        return "上午"
    if 11 <= local_hour < 14:
        return "中午"
    if 14 <= local_hour < 18:
        return "下午"
    if 18 <= local_hour < 23:
        return "晚上"
    return "深夜"


def _diary_user_text(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    cleaned = re.sub(r"^(呃|嗯|哦|啊)[，,。.!！?？\s]*", "", cleaned)
    if not cleaned:
        return ""
    if "请执行白泽幼灵 60 秒 Demo" in cleaned:
        return "你让我跑 60 秒演示脚本，看看我能不能自然开场"
    if re.fullmatch(r"[（(]\s*摸你了\s*[）)]", cleaned):
        return "你摸了摸我"
    if cleaned.strip("。.!！?？,， ") in {"呃", "嗯", "哦", "啊"}:
        return ""
    if cleaned in {"白泽", "小白泽"}:
        return "你叫了我的名字"
    cleaned = ACTION_PARENTHETICAL_PATTERN.sub("", cleaned)
    cleaned = ACTION_BRACKET_PATTERN.sub("", cleaned)
    cleaned = cleaned.strip()
    if len(cleaned) <= 1:
        return ""
    return _compact_text(cleaned, 36)


def _diary_point_score(text: str) -> int:
    if not text:
        return 0
    high_value_keywords = (
        "日记",
        "演示",
        "Demo",
        "开场",
        "调试",
        "工作",
        "很累",
        "不自信",
        "领导",
        "世界杯",
        "葡萄牙",
        "比赛",
        "开发",
        "喜欢一个女生",
        "难过",
        "小伙伴",
        "摸了摸",
    )
    medium_value_keywords = ("连上", "记得", "高兴", "紧张")
    low_value_markers = ("那个", "看一下", "现在是卖了", "后来我说")
    if any(keyword in text for keyword in high_value_keywords):
        return 100
    if any(keyword in text for keyword in medium_value_keywords):
        return 70
    if any(marker in text for marker in low_value_markers):
        return 10
    return 35 if len(text) >= 6 else 0


def _unique_points(values: list[str], limit: int) -> list[str]:
    points = []
    seen = set()
    for value in values:
        normalized = value.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        points.append(normalized)
        if len(points) >= limit:
            break
    return points


def _diary_user_points(dialogues: list[Dict[str, Any]]) -> list[str]:
    candidates = []
    fallback = []
    for index, item in enumerate(dialogues):
        text = _diary_user_text(str(item.get("user_text", "")))
        if not text:
            continue
        if text not in fallback:
            fallback.append(text)
        score = _diary_point_score(text)
        if score >= 60:
            candidates.append((index, score, text))

    selected_by_score = sorted(candidates, key=lambda item: (-item[1], item[0]))[:5]
    selected_indexes = {item[0] for item in selected_by_score}
    selected = [
        text
        for index, _score, text in sorted(selected_by_score, key=lambda item: item[0])
    ]
    if len(selected) < 3:
        for index, text in enumerate(fallback):
            if index in selected_indexes or text in selected or _diary_point_score(text) < 30:
                continue
            selected.append(text)
            if len(selected) >= 5:
                break
    return _unique_points(selected, 5)


def _diary_baize_points(dialogues: list[Dict[str, Any]]) -> list[str]:
    return _unique_points(
        [
            _compact_text(clean_baize_text(str(item.get("baize_text", ""))), 40)
            for item in dialogues
            if item.get("baize_text")
        ],
        3,
    )


def _diary_event_phrase(point: str) -> str:
    text = point.strip("。.!！?？ ")
    rules = [
        (("摸了摸我",), "你轻轻摸了摸我"),
        (("完成了演示",), "你完成了演示这件重要的小事"),
        (("有点紧张", "紧张"), "你有点紧张"),
        (("日记功能",), "我们一起调试日记功能"),
        (("演示脚本", "60 秒演示"), "你让我试着跑演示脚本"),
        (("调试",), "你认真调试我的状态"),
        (("工作", "很累"), "工作把你累得有些没力气"),
        (("不自信", "领导"), "你在工作里有点不自信"),
        (("世界杯",), "你聊起最近在看的世界杯"),
        (("葡萄牙",), "你告诉我真正喜欢的是葡萄牙"),
        (("喜欢一个女生",), "你把心动的小秘密告诉了我"),
        (("难过",), "你把难过也分给我听"),
        (("开发", "AI"), "你说起正在开发 AI 的事情"),
        (("小伙伴",), "你把称呼定成了小伙伴"),
        (("叫了我的名字",), "你叫了我的名字"),
    ]
    for keywords, phrase in rules:
        if any(keyword in text for keyword in keywords):
            return phrase
    return text


def _diary_response_phrase(baize_points: list[str], primary_emotion: str) -> str:
    joined = "。".join(baize_points)
    if "先别急" in joined or "一步步来" in joined or primary_emotion in {"sad", "relaxed", "crying"}:
        return "我陪你稳住心情，也努力把声音放轻，陪你一点点往前走。"
    if "庆祝" in joined or primary_emotion in {"happy", "laughing", "surprised"}:
        return "我替你开心，也想把这份小小的亮光好好留住。"
    if "摸" in joined or "毛茸茸" in joined:
        return "被摸到的时候我有点害羞，又忍不住觉得亲近。"
    if "没太接住" in joined or primary_emotion in {"confused", "thinking"}:
        return "有些话我没能立刻听明白，但还是很认真地跟着你的节奏想。"
    return "我陪你把这些事慢慢接住，也把今天的互动放进心里。"


def _drop_leading_you(text: str) -> str:
    return re.sub(r"^你", "", text.strip())


def _emotion_diary_reaction(primary_emotion: str) -> str:
    if primary_emotion in {"sad", "crying"}:
        return "心里也跟着软了一下，只想把声音放轻一点"
    if primary_emotion in {"relaxed", "sleepy"}:
        return "慢慢安静下来，像把尾巴轻轻圈在身边"
    if primary_emotion in {"happy", "laughing", "funny", "surprised"}:
        return "一下子精神起来，脑袋里都亮了一小盏灯"
    if primary_emotion in {"thinking", "confused"}:
        return "认真转了转小脑袋，努力想把你的意思听明白"
    return "很认真地听着，怕漏掉你话里的小线索"


def _compose_diary_summary(dialogues: list[Dict[str, Any]]) -> str:
    user_points = _diary_user_points(dialogues)
    baize_points = _diary_baize_points(dialogues)
    if not user_points:
        return "今天我和小伙伴短短地打了个招呼，也把这一次相遇记在心里。"

    time_word = _local_time_word(dialogues[0])
    primary_emotion = _primary_emotion(dialogues)
    reaction = _emotion_diary_reaction(primary_emotion)
    event_phrases = _unique_points([_diary_event_phrase(point) for point in user_points], 5)
    if len(user_points) == 1:
        first = f"{time_word}，{event_phrases[0]}。我当时{reaction}，想着一定要好好陪你。"
    else:
        first = (
            f"{time_word}，{event_phrases[0]}，后来{event_phrases[1]}。"
            f"我当时{reaction}，一边听一边把这些小事收进心里。"
        )

    later_events = event_phrases[2:4]
    if later_events:
        second = "后来，" + later_events[0]
        if len(later_events) > 1:
            second += "，还" + _drop_leading_you(later_events[1])
        second += "。"
    else:
        second = "后来我们又安安静静聊了一小会儿。"
    second += _diary_response_phrase(baize_points, primary_emotion)

    final = (
        "现在一天快要收起来了，困意也慢慢靠过来。"
        "但我还是想多陪你一会儿，真希望明天你还能来找我，"
        "聊点轻松的，也聊点只有我们知道的小事。"
    )
    return "\n\n".join([first, second, final])


def _compose_baize_note(primary_emotion: str, dialogue_count: int) -> str:
    if primary_emotion in {"sad", "crying", "relaxed"}:
        return "今天我记得你的不安和努力。下次你再靠近我时，我还会先陪你慢慢呼吸。"
    if primary_emotion in {"happy", "laughing", "funny", "surprised"}:
        return "今天我记得你的亮光和开心。能和你一起庆祝这件小事，我也很高兴。"
    if primary_emotion in {"thinking", "confused"}:
        return "今天我记得我们一起想事情的样子。等你再来，我会继续陪你把问题慢慢理清。"
    if dialogue_count <= 1:
        return "今天我记得这一次小小的相遇。哪怕只聊了一会儿，我也会把它收好。"
    return "今天我记得我们聊过的这些小事。它们会变成我更了解你的线索。"


def generate_diary(
    config: dict,
    diary_date: str | None = None,
    user_id: str | None = None,
    device_id: str | None = None,
) -> Dict[str, Any]:
    db_path = app_mvp_db_path_from_config(config)
    ensure_db(db_path)
    was_created = False
    with _connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        device_id = device_id or DEMO_DEVICE_ID
        user_id = user_id or _latest_bound_user_for_device(conn, device_id)
        shared_device_dialogues = False
        if diary_date is None:
            if shared_device_dialogues:
                latest = conn.execute(
                    """
                    SELECT created_at FROM dialogues
                    WHERE device_id = ?
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (device_id,),
                ).fetchone()
            else:
                latest = conn.execute(
                    """
                    SELECT created_at FROM dialogues
                    WHERE user_id = ? AND device_id = ?
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (user_id, device_id),
                ).fetchone()
            diary_date = _date_from_iso(latest["created_at"]) if latest else today_key()
        dialogues = _dialogues_for_date(conn, user_id, device_id, diary_date, shared_device_dialogues)
        from core.content_safety import evaluate_text

        dialogues = [
            item
            for item in dialogues
            if evaluate_text(config, item.get("user_text", ""), direction="diary").allowed
            and evaluate_text(config, item.get("baize_text", ""), direction="diary").allowed
        ]
        if not dialogues:
            return {}
        quotes = [
            {
                "user_text": item.get("user_text", ""),
                "baize_text": item.get("baize_text", ""),
                "emotion": item.get("emotion", "neutral"),
            }
            for item in dialogues[:3]
        ]
        primary_emotion = _primary_emotion(dialogues)
        summary = _compose_diary_summary(dialogues)
        baize_note = _compose_baize_note(primary_emotion, len(dialogues))
        generated_at = now_iso()
        existing = conn.execute(
            "SELECT id FROM diaries WHERE user_id = ? AND device_id = ? AND date = ?",
            (user_id, device_id, diary_date),
        ).fetchone()
        was_created = existing is None
        diary_id = existing["id"] if existing else f"diary_{uuid.uuid4().hex}"
        conn.execute(
            """
            INSERT INTO diaries(id, user_id, device_id, date, title, summary, primary_emotion, dialogue_count, quotes_json, baize_note, generated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, device_id, date) DO UPDATE SET
                title = excluded.title,
                summary = excluded.summary,
                primary_emotion = excluded.primary_emotion,
                dialogue_count = excluded.dialogue_count,
                quotes_json = excluded.quotes_json,
                baize_note = excluded.baize_note,
                generated_at = excluded.generated_at
            """,
            (
                diary_id,
                user_id,
                device_id,
                diary_date,
                "今天是个好日子哦",
                summary,
                primary_emotion,
                len(dialogues),
                json.dumps(quotes, ensure_ascii=False),
                baize_note,
                generated_at,
            ),
        )
        conn.commit()
    if was_created:
        add_intimacy(config, user_id, device_id, 3, "generate_diary")
    from core.telemetry import diary_generated

    diary_generated()
    return {
        "id": diary_id,
        "date": diary_date,
        "title": "今天是个好日子哦",
        "summary": summary,
        "primary_emotion": primary_emotion,
        "dialogue_count": len(dialogues),
        "quotes": quotes,
        "baize_note": _compose_baize_note(primary_emotion, len(dialogues)),
        "generated_at": generated_at,
    }


def auto_generate_due_diaries(
    config: dict, now: datetime | None = None
) -> list[Dict[str, Any]]:
    """Generate or refresh diaries after the evening quiet window.

    The scan is database-driven, so a service restart catches up recent missed days.
    """
    settings = diary_auto_generation_settings(config)
    if not settings["enabled"]:
        return []

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    local_now = current.astimezone(PRODUCT_TIMEZONE)
    today = local_now.date()
    evening_start = datetime.combine(
        today,
        time(settings["evening_hour"], settings["evening_minute"]),
        tzinfo=PRODUCT_TIMEZONE,
    )
    quiet_period = timedelta(minutes=settings["quiet_period_minutes"])
    earliest_date = today - timedelta(days=settings["lookback_days"] - 1)
    earliest_created_at = datetime.combine(
        earliest_date, time.min, tzinfo=PRODUCT_TIMEZONE
    ).astimezone(timezone.utc).isoformat()

    db_path = app_mvp_db_path_from_config(config)
    ensure_db(db_path)
    grouped: Dict[tuple[str, str, str], list[Dict[str, Any]]] = {}
    existing_counts: Dict[tuple[str, str, str], int] = {}
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT dlg.*
            FROM dialogues dlg
            JOIN user_device_bindings binding
              ON binding.user_id = dlg.user_id AND binding.device_id = dlg.device_id
            WHERE dlg.created_at >= ?
            ORDER BY dlg.created_at ASC
            """,
            (earliest_created_at,),
        ).fetchall()
        for row in rows:
            item = dict(row)
            if is_legacy_xiaozhi_dialogue(item):
                continue
            diary_date = _date_from_iso(item["created_at"])
            try:
                day = date.fromisoformat(diary_date)
            except ValueError:
                continue
            if day < earliest_date or day > today:
                continue
            key = (item["user_id"], item["device_id"], diary_date)
            grouped.setdefault(key, []).append(item)

        if grouped:
            diary_rows = conn.execute(
                """
                SELECT user_id, device_id, date, dialogue_count
                FROM diaries
                WHERE date >= ? AND date <= ?
                """,
                (earliest_date.isoformat(), today.isoformat()),
            ).fetchall()
            existing_counts = {
                (row["user_id"], row["device_id"], row["date"]): int(
                    row["dialogue_count"]
                )
                for row in diary_rows
            }

    generated = []
    for (user_id, device_id, diary_date), dialogues in grouped.items():
        if existing_counts.get((user_id, device_id, diary_date), 0) >= len(dialogues):
            continue
        latest_at = datetime.fromisoformat(
            dialogues[-1]["created_at"].replace("Z", "+00:00")
        )
        if latest_at.tzinfo is None:
            latest_at = latest_at.replace(tzinfo=timezone.utc)
        if current - latest_at.astimezone(timezone.utc) < quiet_period:
            continue
        day = date.fromisoformat(diary_date)
        if day == today and local_now < evening_start:
            continue
        diary = generate_diary(
            config,
            diary_date=diary_date,
            user_id=user_id,
            device_id=device_id,
        )
        if diary:
            generated.append(diary)
    return generated


def list_diaries(config: dict, user_id: str, device_id: str) -> list[Dict[str, Any]] | None:
    db_path = app_mvp_db_path_from_config(config)
    ensure_db(db_path)
    with _connect(db_path) as conn:
        if not _is_bound_conn(conn, user_id, device_id):
            return None
        rows = conn.execute(
            """
            SELECT * FROM diaries
            WHERE user_id = ? AND device_id = ?
            ORDER BY date DESC LIMIT 30
            """,
            (user_id, device_id),
        ).fetchall()
        return [_diary_payload(row) for row in rows]


def list_memories(config: dict, user_id: str, device_id: str) -> list[Dict[str, Any]] | None:
    from core.api.app_memory_store import list_memories as list_memory_items

    page = list_memory_items(config, user_id, device_id)
    return None if page is None else page["items"]


def list_memories_page(
    config: dict, user_id: str, device_id: str, **filters
) -> Dict[str, Any] | None:
    from core.api.app_memory_store import list_memories as list_memory_items

    return list_memory_items(config, user_id, device_id, **filters)


def upsert_memory(
    config: dict,
    user_id: str,
    device_id: str,
    category: str,
    content: str,
    memory_id: str | None = None,
    **values,
) -> Dict[str, Any] | None:
    from core.api.app_memory_store import create_memory, update_memory

    if memory_id:
        update_values = dict(values)
        update_values.update({"category": category, "content": content})
        return update_memory(
            config, user_id, device_id, memory_id, update_values
        )
    return create_memory(
        config,
        user_id,
        device_id,
        content=content,
        memory_type=category,
        scope=values.get("scope", "relationship"),
        key=values.get("key"),
        importance=values.get("importance", 70),
        pinned=values.get("pinned", False),
        expires_at=values.get("expires_at"),
        occurred_at=values.get("occurred_at"),
    )


def extract_memories_from_dialogue(
    config: dict,
    user_id: str,
    device_id: str,
    user_text: str,
    baize_text: str = "",
) -> list[Dict[str, Any]]:
    text = (user_text or "").strip()
    if not text:
        return []
    candidates: list[tuple[str, str]] = []
    lowered = text.lower()
    if any(word in text for word in ("喜欢", "爱吃", "想要", "偏好")) or any(word in lowered for word in ("like", "love", "prefer")):
        candidates.append(("preference", text[:120]))
    if any(word in text for word in ("叫我", "称呼我", "我的名字", "我叫")):
        candidates.append(("nickname", text[:120]))
    if any(word in text for word in ("今天", "明天", "昨天", "完成", "去了", "遇到", "考试", "工作")):
        candidates.append(("event", text[:120]))
    if any(word in text for word in ("开心", "难过", "紧张", "生气", "害怕", "累", "焦虑")):
        candidates.append(("emotion", text[:120]))
    memories = []
    for category, content in candidates[:2]:
        try:
            memory = upsert_memory(config, user_id, device_id, category, content)
            if memory:
                memories.append(memory)
        except Exception:
            continue
    return memories


def delete_memory(config: dict, user_id: str, device_id: str, memory_id: str) -> bool | None:
    from core.api.app_memory_store import delete_memory as delete_memory_item

    return delete_memory_item(config, user_id, device_id, memory_id)


def user_summary(config: dict, user_id: str) -> Dict[str, Any]:
    db_path = app_mvp_db_path_from_config(config)
    ensure_db(db_path)
    with _connect(db_path) as conn:
        sync_configured_admin_roles(config, conn)
        user_row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not user_row:
            return {}
        spirit_power = _spirit_power_payload(conn, user_id, config)
        payload = {
            **_row_to_user(user_row),
            "spirit_power": spirit_power,
            "energy": spirit_power,
            "intimacy": intimacy_payload(conn, user_id),
        }
    from core.api.app_memory_store import user_memory_overview

    payload["memory"] = user_memory_overview(config, user_id)
    return payload


def ota_payload(config: dict, user_id: str, device_id: str) -> Dict[str, Any] | None:
    db_path = app_mvp_db_path_from_config(config)
    ensure_db(db_path)
    with _connect(db_path) as conn:
        if not _is_bound_conn(conn, user_id, device_id):
            return None
        row = conn.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()
        current_version = row["current_version"] or row["firmware_version"]
        latest_version = latest_firmware_version_for_model(config, row["model"]) or row["latest_version"] or current_version
        update_available = _is_higher_firmware_version(latest_version, current_version)
        release_note = (
            f"发现可用固件版本 {latest_version}"
            if update_available
            else f"设备当前版本 {current_version}"
        )
        return {
            "device_id": row["id"],
            "current_version": current_version,
            "latest_version": latest_version,
            "update_available": update_available,
            "release_note": release_note,
        }


def update_device_report(
    config: dict,
    source_device_id: str,
    client_id: str = "",
    model: str = "",
    firmware_version: str = "",
    battery_percent: int | None = None,
    activity_status: str | None = None,
) -> Dict[str, Any]:
    db_path = app_mvp_db_path_from_config(config)
    ensure_db(db_path)
    reported_at = now_iso()
    with _connect(db_path) as conn:
        row = _find_device_row_for_source(conn, source_device_id=source_device_id, client_id=client_id)
        if row is None:
            if (source_device_id or "").strip() or (client_id or "").strip():
                row = _claimable_seed_device_row(conn)
                if row is None:
                    row = _insert_device(
                        conn,
                        display_name="我的白泽",
                        source_device_id=source_device_id,
                        client_id=client_id,
                        model=model,
                        firmware_version=firmware_version,
                        online_status="online",
                    )
            else:
                row = conn.execute("SELECT * FROM devices WHERE id = ?", (DEMO_DEVICE_ID,)).fetchone()
        device_id = row["id"]
        clean_battery = max(0, min(100, int(battery_percent))) if battery_percent is not None else row["battery_percent"]
        clean_version = firmware_version or row["firmware_version"]
        clean_activity_status = (activity_status or row["activity_status"] or "unknown").strip() or "unknown"
        latest_version = firmware_version if firmware_version and not row["update_available"] else row["latest_version"]
        release_note = f"设备当前版本 {firmware_version}" if firmware_version and not row["update_available"] else row["release_note"]
        conn.execute(
            """
            UPDATE devices
            SET source_device_id = ?, client_id = ?, model = ?, firmware_version = ?,
                battery_percent = ?, activity_status = ?, online_status = 'online', last_online_at = ?,
                current_version = ?, latest_version = ?, release_note = ?
            WHERE id = ?
            """,
            (
                source_device_id or row["source_device_id"],
                client_id or row["client_id"],
                model or row["model"],
                clean_version,
                clean_battery,
                clean_activity_status,
                reported_at,
                clean_version,
                latest_version,
                release_note,
                device_id,
            ),
        )
        conn.commit()
        return _legacy_device(conn, conn.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone())


def device_activation_state(
    config: dict,
    source_device_id: str = "",
    client_id: str = "",
) -> Dict[str, Any] | None:
    """Return the OTA activation state for a physical device.

    The public firmware identifies itself with Device-Id and Client-Id headers.
    A device is activated once any App user owns it through
    user_device_bindings.
    """
    source_device_id = (source_device_id or "").strip()
    client_id = (client_id or "").strip()
    if not source_device_id and not client_id:
        return None

    db_path = app_mvp_db_path_from_config(config)
    ensure_db(db_path)
    with _connect(db_path) as conn:
        row = _find_device_row_for_source(
            conn,
            source_device_id=source_device_id,
            client_id=client_id,
        )
        if row is None:
            return None
        owner = conn.execute(
            """
            SELECT user_id, bound_at
            FROM user_device_bindings
            WHERE device_id = ?
            ORDER BY bound_at DESC, rowid DESC
            LIMIT 1
            """,
            (row["id"],),
        ).fetchone()
        return {
            "device": device_payload(row),
            "activated": owner is not None,
            "bound_user_id": owner["user_id"] if owner else None,
            "bound_at": owner["bound_at"] if owner else None,
        }


def update_ota_report(config: dict, current_version: str, latest_version: str, update_available: bool, release_note: str) -> Dict[str, Any]:
    db_path = app_mvp_db_path_from_config(config)
    ensure_db(db_path)
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM devices WHERE id = ?", (DEMO_DEVICE_ID,)).fetchone()
        conn.execute(
            """
            UPDATE devices
            SET current_version = ?, firmware_version = ?, latest_version = ?,
                update_available = ?, release_note = ?
            WHERE id = ?
            """,
            (
                current_version or row["current_version"],
                current_version or row["firmware_version"],
                latest_version or row["latest_version"],
                1 if update_available else 0,
                release_note or row["release_note"],
                DEMO_DEVICE_ID,
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM devices WHERE id = ?", (DEMO_DEVICE_ID,)).fetchone()
        return {
            "current_version": row["current_version"],
            "latest_version": row["latest_version"],
            "update_available": bool(row["update_available"]),
            "release_note": row["release_note"],
        }


def list_energy_events(config: dict, limit: int = 100) -> list[Dict[str, Any]]:
    db_path = app_mvp_db_path_from_config(config)
    ensure_db(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT e.*, u.phone, u.nickname
            FROM energy_events e
            LEFT JOIN users u ON u.id = e.user_id
            ORDER BY e.created_at DESC LIMIT ?
            """,
            (max(1, min(int(limit), 500)),),
        ).fetchall()
        return [dict(row) for row in rows]


def list_intimacy_events(config: dict, limit: int = 100) -> list[Dict[str, Any]]:
    db_path = app_mvp_db_path_from_config(config)
    ensure_db(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT e.*, u.phone, u.nickname
            FROM intimacy_events e
            LEFT JOIN users u ON u.id = e.user_id
            ORDER BY e.created_at DESC LIMIT ?
            """,
            (max(1, min(int(limit), 500)),),
        ).fetchall()
        return [dict(row) for row in rows]


def list_admin_conversations(config: dict, limit: int = 100) -> list[Dict[str, Any]]:
    db_path = app_mvp_db_path_from_config(config)
    ensure_db(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT d.*, u.phone, u.nickname, dev.display_name
            FROM dialogues d
            LEFT JOIN users u ON u.id = d.user_id
            LEFT JOIN devices dev ON dev.id = d.device_id
            ORDER BY d.created_at DESC LIMIT ?
            """,
            (max(1, min(int(limit), 500)),),
        ).fetchall()
        return [dict(row) for row in rows]


def admin_metrics(config: dict) -> Dict[str, Any]:
    db_path = app_mvp_db_path_from_config(config)
    ensure_db(db_path)
    with _connect(db_path) as conn:
        payload = {
            "users": conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"],
            "bound_devices": conn.execute("SELECT COUNT(*) AS c FROM user_device_bindings").fetchone()["c"],
            "dialogues": conn.execute("SELECT COUNT(*) AS c FROM dialogues").fetchone()["c"],
            "diaries": conn.execute("SELECT COUNT(*) AS c FROM diaries").fetchone()["c"],
            "energy_consumed": abs(conn.execute("SELECT COALESCE(SUM(delta), 0) AS c FROM energy_events WHERE delta < 0").fetchone()["c"]),
            "emotion_hits": {
                row["emotion"]: row["c"]
                for row in conn.execute("SELECT emotion, COUNT(*) AS c FROM emotion_stats GROUP BY emotion").fetchall()
            },
            "phone_users": conn.execute("SELECT COUNT(*) AS c FROM users WHERE phone IS NOT NULL AND phone != ''").fetchone()["c"],
            "open_tickets": conn.execute("SELECT COUNT(*) AS c FROM support_tickets WHERE status IN ('open', 'in_progress')").fetchone()["c"],
        }
    from core.api.app_memory_store import memory_metrics_snapshot

    payload.update(memory_metrics_snapshot(config))
    return payload


def prompt_context_for_device(config: dict, device_identifier: str) -> str:
    if not device_identifier:
        return ""
    db_path = app_mvp_db_path_from_config(config)
    ensure_db(db_path)
    with _connect(db_path) as conn:
        device = conn.execute(
            "SELECT * FROM devices WHERE id = ? OR source_device_id = ? OR client_id = ?",
            (device_identifier, device_identifier, device_identifier),
        ).fetchone()
        if not device:
            return ""
        settings = _settings_payload(
            conn.execute("SELECT * FROM device_settings WHERE device_id = ?", (device["id"],)).fetchone(),
            device["id"],
        )
        memories = [
            dict(row)
            for row in conn.execute(
                """
                SELECT category, content FROM memories
                WHERE device_id = ? AND disabled_at IS NULL
                ORDER BY created_at DESC LIMIT 8
                """,
                (device["id"],),
            ).fetchall()
        ]
    memory_context = "\n".join(f"- {item['category']}: {item['content']}" for item in memories)
    if memory_context:
        settings["tts_voice"] = f"{settings['tts_voice']}\n记忆：\n{memory_context}"
    return "\n".join(
        [
            "",
            "<app_device_settings>",
            f"白泽昵称：{settings['baize_nickname']}",
            f"用户称呼：{settings['user_call_name']}",
            f"性格模式：{settings['personality_mode']}",
            f"TTS 音色：{settings['tts_voice']}",
            "回复保持 1 到 2 句，先接住用户情绪，不输出括号动作或心理描写。",
            "</app_device_settings>",
        ]
    )


def _legacy_device(conn: sqlite3.Connection, row: sqlite3.Row) -> Dict[str, Any]:
    device = device_payload(row)
    device["settings"] = _settings_payload(
        conn.execute("SELECT * FROM device_settings WHERE device_id = ?", (row["id"],)).fetchone(),
        row["id"],
    )
    device["ota"] = {
        "current_version": row["current_version"],
        "latest_version": row["latest_version"],
        "update_available": bool(row["update_available"]),
        "release_note": row["release_note"],
    }
    device["dialogues"] = [
        dict(item)
        for item in conn.execute(
            "SELECT * FROM dialogues WHERE device_id = ? ORDER BY created_at DESC LIMIT 100",
            (row["id"],),
        ).fetchall()
        if not is_legacy_xiaozhi_dialogue(dict(item))
    ]
    device["diaries"] = [
        _diary_payload(item)
        for item in conn.execute(
            "SELECT * FROM diaries WHERE device_id = ? ORDER BY date DESC LIMIT 30",
            (row["id"],),
        ).fetchall()
    ]
    device["memories"] = [
        dict(item)
        for item in conn.execute(
            "SELECT id, category, content, created_at FROM memories WHERE device_id = ? AND disabled_at IS NULL",
            (row["id"],),
        ).fetchall()
    ]
    return device


def default_state() -> Dict[str, Any]:
    return load_state(os.path.join(os.getcwd(), "data", "app_mvp.sqlite3"))


def merge_defaults(state: Dict[str, Any]) -> Dict[str, Any]:
    return state


def load_state(path: str) -> Dict[str, Any]:
    db_path = db_path_from_state_path(path)
    ensure_db(db_path)
    with _connect(db_path) as conn:
        users = {row["id"]: _row_to_user(row) for row in conn.execute("SELECT * FROM users").fetchall()}
        devices = {row["id"]: _legacy_device(conn, row) for row in conn.execute("SELECT * FROM devices").fetchall()}
        bindings: Dict[str, list[str]] = {user_id: [] for user_id in users}
        for row in conn.execute("SELECT * FROM user_device_bindings ORDER BY bound_at").fetchall():
            bindings.setdefault(row["user_id"], []).append(row["device_id"])
    return {"users": users, "bindings": bindings, "devices": devices}


def save_state(path: str, state: Dict[str, Any]) -> None:
    db_path = db_path_from_state_path(path)
    ensure_db(db_path)
    with _connect(db_path) as conn:
        for user_id, user in state.get("users", {}).items():
            now = now_iso()
            conn.execute(
                """
                INSERT INTO users(id, nickname, login_type, invite_code, created_at, last_login_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET nickname = excluded.nickname
                """,
                (
                    user_id,
                    user.get("nickname") or user.get("display_name") or "User",
                    user.get("login_type") or "demo",
                    user.get("invite_code") or DEFAULT_INVITE_CODE,
                    user.get("created_at") or now,
                    user.get("last_login_at") or now,
                ),
            )
        for user_id, device_ids in state.get("bindings", {}).items():
            for device_id in device_ids:
                conn.execute(
                    "INSERT OR IGNORE INTO user_device_bindings(user_id, device_id, bound_at) VALUES (?, ?, ?)",
                    (user_id, device_id, now_iso()),
                )
        for device_id, device in state.get("devices", {}).items():
            conn.execute(
                """
                INSERT INTO devices(id, device_code, display_name, online_status, activity_status, battery_percent, firmware_version, last_online_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    display_name = excluded.display_name,
                    online_status = excluded.online_status,
                    activity_status = excluded.activity_status,
                    battery_percent = excluded.battery_percent,
                    firmware_version = excluded.firmware_version,
                    last_online_at = excluded.last_online_at
                """,
                (
                    device_id,
                    device.get("device_code") or DEMO_DEVICE_CODE,
                    device.get("display_name") or "我的白泽",
                    device.get("online_status") or "unknown",
                    device.get("activity_status") or "unknown",
                    device.get("battery_percent"),
                    device.get("firmware_version") or "0.1.0-demo",
                    device.get("last_online_at"),
                ),
            )
            settings = device.get("settings") or {}
            conn.execute(
                """
                INSERT INTO device_settings(device_id, baize_nickname, user_call_name, personality_mode, tts_voice)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(device_id) DO UPDATE SET
                    baize_nickname = excluded.baize_nickname,
                    user_call_name = excluded.user_call_name,
                    personality_mode = excluded.personality_mode,
                    tts_voice = excluded.tts_voice
                """,
                (
                    device_id,
                    settings.get("baize_nickname", "白泽"),
                    settings.get("user_call_name", "小伙伴"),
                    settings.get("personality_mode", "curious"),
                    settings.get("tts_voice", "Sambert 知颖"),
                ),
            )
            for item in device.get("dialogues", []):
                conn.execute(
                    """
                    INSERT OR REPLACE INTO dialogues(id, user_id, device_id, source_device_id, session_id, user_text, baize_text, emotion, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item.get("id") or f"dlg_{uuid.uuid4().hex}",
                        item.get("user_id") or DEMO_USER_ID,
                        device_id,
                        item.get("source_device_id"),
                        item.get("session_id"),
                        item.get("user_text") or "",
                        clean_baize_text(item.get("baize_text") or ""),
                        normalize_emotion(item.get("emotion")),
                        item.get("created_at") or now_iso(),
                    ),
                )
        conn.commit()
