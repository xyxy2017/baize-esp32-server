import json
import os
import re
import hashlib
import hmac
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict


DEMO_TOKEN = "demo-token"
DEMO_USER_ID = "demo_user"
DEMO_DEVICE_CODE = "123456"
DEMO_DEVICE_ID = "baize_dev_001"
DEFAULT_INVITE_CODE = "BAIZE-MVP"
INITIAL_ENERGY = 30
DAILY_ENERGY_LIMIT = 30
PASSWORD_MIN_LENGTH = 6
PHONE_PATTERN = re.compile(r"^1[3-9]\d{9}$")
USER_ROLE = "user"
ADMIN_ROLE = "admin"
VALID_ROLES = {USER_ROLE, ADMIN_ROLE}

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


def today_key() -> str:
    return datetime.now(timezone.utc).date().isoformat()


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
        CREATE TABLE IF NOT EXISTS device_settings (
            device_id TEXT PRIMARY KEY,
            baize_nickname TEXT NOT NULL,
            user_call_name TEXT NOT NULL,
            personality_mode TEXT NOT NULL,
            tts_voice TEXT NOT NULL DEFAULT 'Sambert 知颖'
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
            created_at TEXT NOT NULL
        );
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
            last_recovered_on TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS energy_events (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            device_id TEXT,
            delta INTEGER NOT NULL,
            reason TEXT NOT NULL,
            created_at TEXT NOT NULL
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
        """
    )
    _migrate_schema(conn)
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


def _ensure_energy(conn: sqlite3.Connection, user_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM energy_accounts WHERE user_id = ?", (user_id,)).fetchone()
    today = today_key()
    now = now_iso()
    if row is None:
        conn.execute(
            "INSERT INTO energy_accounts(user_id, current_energy, daily_limit, updated_at, last_recovered_on) VALUES (?, ?, ?, ?, ?)",
            (user_id, INITIAL_ENERGY, DAILY_ENERGY_LIMIT, now, today),
        )
        conn.commit()
    elif row["last_recovered_on"] != today:
        conn.execute(
            "UPDATE energy_accounts SET current_energy = daily_limit, updated_at = ?, last_recovered_on = ? WHERE user_id = ?",
            (now, today, user_id),
        )
        conn.execute(
            "INSERT INTO energy_events(id, user_id, delta, reason, created_at) VALUES (?, ?, ?, 'daily_recover', ?)",
            (f"energy_{uuid.uuid4().hex}", user_id, row["daily_limit"], now),
        )
        conn.commit()
    return conn.execute("SELECT * FROM energy_accounts WHERE user_id = ?", (user_id,)).fetchone()


def _energy_payload(conn: sqlite3.Connection, user_id: str) -> Dict[str, Any]:
    row = _ensure_energy(conn, user_id)
    return {
        "current": row["current_energy"],
        "daily_limit": row["daily_limit"],
        "updated_at": row["updated_at"],
        "last_recovered_on": row["last_recovered_on"],
    }


def consume_energy(config: dict, user_id: str, device_id: str | None, amount: int, reason: str) -> bool:
    db_path = app_mvp_db_path_from_config(config)
    ensure_db(db_path)
    with _connect(db_path) as conn:
        account = _ensure_energy(conn, user_id)
        if account["current_energy"] < amount:
            return False
        now = now_iso()
        conn.execute("UPDATE energy_accounts SET current_energy = current_energy - ?, updated_at = ? WHERE user_id = ?", (amount, now, user_id))
        conn.execute(
            "INSERT INTO energy_events(id, user_id, device_id, delta, reason, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (f"energy_{uuid.uuid4().hex}", user_id, device_id, -amount, reason, now),
        )
        conn.commit()
    return True


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
        _ensure_energy(conn, user_id)
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


def register_phone_user(config: dict, phone: str, password: str, nickname: str = "") -> Dict[str, Any]:
    phone = validate_phone(phone)
    password = _validate_password(password)
    nickname = (nickname or "").strip() or mask_phone(phone)
    db_path = app_mvp_db_path_from_config(config)
    ensure_db(db_path)
    now = now_iso()
    role = role_for_phone(config, phone)
    with _connect(db_path) as conn:
        sync_configured_admin_roles(config, conn)
        exists = conn.execute("SELECT 1 FROM users WHERE phone = ?", (phone,)).fetchone()
        if exists:
            raise ValueError("phone 已注册")
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
        _ensure_energy(conn, user_id)
        token, expires_at = _issue_token(conn, user_id, now)
        conn.commit()
        user = _row_to_user(conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())
    return {"token": token, "expires_at": expires_at, "user": user}


def login_phone_user(config: dict, phone: str, password: str) -> Dict[str, Any]:
    phone = validate_phone(phone)
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
        _ensure_energy(conn, row["id"])
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
        if row["password_hash"] and not _verify_password(old_password or "", row["password_hash"]):
            return False
        now = now_iso()
        conn.execute(
            "UPDATE users SET password_hash = ?, password_updated_at = ?, login_type = 'phone_password' WHERE id = ?",
            (_hash_password(new_password), now, user_id),
        )
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
        }
    return {
        "device_id": device_id,
        "baize_nickname": row["baize_nickname"],
        "user_call_name": row["user_call_name"],
        "personality_mode": row["personality_mode"],
        "tts_voice": row["tts_voice"],
    }


def device_payload(row: sqlite3.Row) -> Dict[str, Any]:
    return {
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


def _is_bound_conn(conn: sqlite3.Connection, user_id: str, device_id: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM user_device_bindings WHERE user_id = ? AND device_id = ?",
        (user_id, device_id),
    ).fetchone() is not None


def demo_auto_bind_new_devices_to_all_users(config: dict) -> bool:
    app_mvp = config.get("app_mvp", {}) or {}
    app_demo = config.get("app_demo", {}) or {}
    if "demo_auto_bind_new_devices_to_all_users" in app_mvp:
        return bool(app_mvp["demo_auto_bind_new_devices_to_all_users"])
    if "demo_auto_bind_new_devices_to_all_users" in app_demo:
        return bool(app_demo["demo_auto_bind_new_devices_to_all_users"])
    return True


def _bind_device_to_all_users(conn: sqlite3.Connection, device_id: str) -> None:
    bound_at = now_iso()
    rows = conn.execute("SELECT id FROM users ORDER BY created_at, id").fetchall()
    for row in rows:
        user_id = row["id"]
        conn.execute(
            "INSERT OR IGNORE INTO user_device_bindings(user_id, device_id, bound_at) VALUES (?, ?, ?)",
            (user_id, device_id, bound_at),
        )
        _ensure_intimacy(conn, user_id, device_id)


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
        _ensure_intimacy(conn, user_id, row["id"])
        conn.commit()
        return device_payload(row)


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
            SELECT d.* FROM devices d
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
            SELECT d.* FROM devices d
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
        cur = conn.execute(
            "DELETE FROM user_device_bindings WHERE user_id = ? AND device_id = ?",
            (user_id, device_id),
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


def update_settings(config: dict, user_id: str, device_id: str, values: Dict[str, str]) -> Dict[str, Any] | None:
    db_path = app_mvp_db_path_from_config(config)
    ensure_db(db_path)
    with _connect(db_path) as conn:
        if not _is_bound_conn(conn, user_id, device_id):
            return None
        current = _settings_payload(conn.execute("SELECT * FROM device_settings WHERE device_id = ?", (device_id,)).fetchone(), device_id)
        for key in ("baize_nickname", "user_call_name", "personality_mode", "tts_voice"):
            if values.get(key) is not None:
                current[key] = values[key]
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
            (device_id, current["baize_nickname"], current["user_call_name"], current["personality_mode"], current["tts_voice"]),
        )
        conn.commit()
        return current


def _date_from_iso(value: str) -> str:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date().isoformat()
    except Exception:
        return today_key()


def _first_bound_user_for_device(conn: sqlite3.Connection, device_id: str) -> str:
    row = conn.execute(
        "SELECT user_id FROM user_device_bindings WHERE device_id = ? ORDER BY bound_at LIMIT 1",
        (device_id,),
    ).fetchone()
    return row["user_id"] if row else DEMO_USER_ID


def _shared_device_dialogues_enabled(config: dict) -> bool:
    """Demo mode: a real test device is visible to every bound iOS account."""
    return demo_auto_bind_new_devices_to_all_users(config)


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
            ORDER BY b.bound_at LIMIT 1
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
) -> Dict[str, Any]:
    user_text = (user_text or "").strip()
    inferred_emotion = infer_emotion(baize_text, emotion or "neutral")
    baize_text = clean_baize_text(baize_text)
    if not user_text or not baize_text:
        return {}

    db_path = app_mvp_db_path_from_config(config)
    ensure_db(db_path)
    with _connect(db_path) as conn:
        device_id = device_id or _device_id_for_source(conn, source_device_id)
        user_id = user_id or _first_bound_user_for_device(conn, device_id)
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
            "created_at": created_at,
        }
        conn.execute(
            """
            INSERT INTO dialogues(id, user_id, device_id, source_device_id, session_id, user_text, baize_text, emotion, created_at)
            VALUES (:id, :user_id, :device_id, :source_device_id, :session_id, :user_text, :baize_text, :emotion, :created_at)
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
        conn.commit()
    record_dialogue_intimacy(config, user_id, device_id)
    extract_memories_from_dialogue(config, user_id, device_id, user_text, baize_text)
    return item


def list_dialogues(config: dict, user_id: str, device_id: str) -> list[Dict[str, Any]] | None:
    db_path = app_mvp_db_path_from_config(config)
    ensure_db(db_path)
    with _connect(db_path) as conn:
        if not _is_bound_conn(conn, user_id, device_id):
            return None
        if _shared_device_dialogues_enabled(config):
            rows = conn.execute(
                """
                SELECT * FROM dialogues
                WHERE device_id = ?
                ORDER BY created_at DESC LIMIT 100
                """,
                (device_id,),
            ).fetchall()
        else:
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
    with _connect(db_path) as conn:
        device_id = device_id or DEMO_DEVICE_ID
        user_id = user_id or _first_bound_user_for_device(conn, device_id)
        shared_device_dialogues = _shared_device_dialogues_enabled(config) and _is_bound_conn(conn, user_id, device_id)
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
    add_intimacy(config, user_id, device_id, 3, "generate_diary")
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
    db_path = app_mvp_db_path_from_config(config)
    ensure_db(db_path)
    with _connect(db_path) as conn:
        if not _is_bound_conn(conn, user_id, device_id):
            return None
        rows = conn.execute(
            """
            SELECT id, category, content, created_at FROM memories
            WHERE user_id = ? AND device_id = ? AND disabled_at IS NULL
            ORDER BY created_at DESC
            """,
            (user_id, device_id),
        ).fetchall()
        return [dict(row) for row in rows]


def upsert_memory(
    config: dict,
    user_id: str,
    device_id: str,
    category: str,
    content: str,
    memory_id: str | None = None,
) -> Dict[str, Any] | None:
    category = (category or "note").strip()
    content = (content or "").strip()
    if not content:
        raise ValueError("content 不能为空")
    if category not in {"preference", "nickname", "event", "emotion", "note"}:
        category = "note"
    db_path = app_mvp_db_path_from_config(config)
    ensure_db(db_path)
    with _connect(db_path) as conn:
        if not _is_bound_conn(conn, user_id, device_id):
            return None
        now = now_iso()
        if memory_id:
            cur = conn.execute(
                """
                UPDATE memories SET category = ?, content = ?
                WHERE id = ? AND user_id = ? AND device_id = ? AND disabled_at IS NULL
                """,
                (category, content, memory_id, user_id, device_id),
            )
            if cur.rowcount == 0:
                return None
        else:
            duplicate = conn.execute(
                """
                SELECT id FROM memories
                WHERE user_id = ? AND device_id = ? AND category = ? AND content = ? AND disabled_at IS NULL
                """,
                (user_id, device_id, category, content),
            ).fetchone()
            memory_id = duplicate["id"] if duplicate else f"mem_{uuid.uuid4().hex}"
            if duplicate is None:
                conn.execute(
                    "INSERT INTO memories(id, user_id, device_id, category, content, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (memory_id, user_id, device_id, category, content, now),
                )
        conn.commit()
        row = conn.execute(
            "SELECT id, category, content, created_at FROM memories WHERE id = ?",
            (memory_id,),
        ).fetchone()
        return dict(row) if row else None


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
    db_path = app_mvp_db_path_from_config(config)
    ensure_db(db_path)
    with _connect(db_path) as conn:
        if not _is_bound_conn(conn, user_id, device_id):
            return None
        cur = conn.execute(
            """
            UPDATE memories SET disabled_at = ?
            WHERE user_id = ? AND device_id = ? AND id = ? AND disabled_at IS NULL
            """,
            (now_iso(), user_id, device_id, memory_id),
        )
        conn.commit()
        return cur.rowcount > 0


def user_summary(config: dict, user_id: str) -> Dict[str, Any]:
    db_path = app_mvp_db_path_from_config(config)
    ensure_db(db_path)
    with _connect(db_path) as conn:
        sync_configured_admin_roles(config, conn)
        user_row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not user_row:
            return {}
        return {
            **_row_to_user(user_row),
            "energy": _energy_payload(conn, user_id),
            "intimacy": intimacy_payload(conn, user_id),
        }


def ota_payload(config: dict, user_id: str, device_id: str) -> Dict[str, Any] | None:
    db_path = app_mvp_db_path_from_config(config)
    ensure_db(db_path)
    with _connect(db_path) as conn:
        if not _is_bound_conn(conn, user_id, device_id):
            return None
        row = conn.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()
        return {
            "device_id": row["id"],
            "current_version": row["current_version"],
            "latest_version": row["latest_version"],
            "update_available": bool(row["update_available"]),
            "release_note": row["release_note"],
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
        should_auto_bind_all_users = False
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
                    should_auto_bind_all_users = demo_auto_bind_new_devices_to_all_users(config)
                else:
                    should_auto_bind_all_users = demo_auto_bind_new_devices_to_all_users(config)
            else:
                row = conn.execute("SELECT * FROM devices WHERE id = ?", (DEMO_DEVICE_ID,)).fetchone()
        device_id = row["id"]
        if should_auto_bind_all_users:
            _bind_device_to_all_users(conn, device_id)
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
        return {
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
        }


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
