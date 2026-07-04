import json
import os
import re
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
    conn.commit()


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
    return {
        "id": row["id"],
        "nickname": row["nickname"],
        "display_name": row["nickname"],
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


def user_for_token(config: dict, token: str) -> Dict[str, Any] | None:
    if not token:
        return None
    db_path = app_mvp_db_path_from_config(config)
    ensure_db(db_path)
    with _connect(db_path) as conn:
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
        "battery_percent": row["battery_percent"],
        "firmware_version": row["firmware_version"],
        "last_online_at": row["last_online_at"],
    }


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
        conn.execute(
            "INSERT OR IGNORE INTO user_device_bindings(user_id, device_id, bound_at) VALUES (?, ?, ?)",
            (user_id, row["id"], now_iso()),
        )
        _ensure_intimacy(conn, user_id, row["id"])
        conn.commit()
        return device_payload(row)


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


def _device_id_for_source(conn: sqlite3.Connection, source_device_id: str) -> str:
    if source_device_id:
        row = conn.execute(
            "SELECT id FROM devices WHERE source_device_id = ? OR client_id = ?",
            (source_device_id, source_device_id),
        ).fetchone()
        if row:
            return row["id"]
    return DEMO_DEVICE_ID


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


def _dialogues_for_date(conn: sqlite3.Connection, user_id: str, device_id: str, diary_date: str) -> list[Dict[str, Any]]:
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
        if diary_date is None:
            latest = conn.execute(
                """
                SELECT created_at FROM dialogues
                WHERE user_id = ? AND device_id = ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (user_id, device_id),
            ).fetchone()
            diary_date = _date_from_iso(latest["created_at"]) if latest else today_key()
        dialogues = _dialogues_for_date(conn, user_id, device_id, diary_date)
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
        user_points = [item.get("user_text", "") for item in dialogues[:3] if item.get("user_text")]
        baize_points = [item.get("baize_text", "") for item in dialogues[:2] if item.get("baize_text")]
        summary = "；".join(user_points)
        if baize_points:
            summary = f"{summary}。白泽回应：{'；'.join(baize_points)}"
        primary_emotion = _primary_emotion(dialogues)
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
                f"{diary_date} 的白泽小记",
                summary,
                primary_emotion,
                len(dialogues),
                json.dumps(quotes, ensure_ascii=False),
                "今天也有好好聊过啦，小伙伴。",
                generated_at,
            ),
        )
        conn.commit()
    add_intimacy(config, user_id, device_id, 3, "generate_diary")
    return {
        "id": diary_id,
        "date": diary_date,
        "title": f"{diary_date} 的白泽小记",
        "summary": summary,
        "primary_emotion": primary_emotion,
        "dialogue_count": len(dialogues),
        "quotes": quotes,
        "baize_note": "今天也有好好聊过啦，小伙伴。",
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
) -> Dict[str, Any]:
    db_path = app_mvp_db_path_from_config(config)
    ensure_db(db_path)
    reported_at = now_iso()
    with _connect(db_path) as conn:
        device_id = _device_id_for_source(conn, source_device_id)
        row = conn.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()
        clean_battery = max(0, min(100, int(battery_percent))) if battery_percent is not None else row["battery_percent"]
        clean_version = firmware_version or row["firmware_version"]
        latest_version = firmware_version if firmware_version and not row["update_available"] else row["latest_version"]
        release_note = f"设备当前版本 {firmware_version}" if firmware_version and not row["update_available"] else row["release_note"]
        conn.execute(
            """
            UPDATE devices
            SET source_device_id = ?, client_id = ?, model = ?, firmware_version = ?,
                battery_percent = ?, online_status = 'online', last_online_at = ?,
                current_version = ?, latest_version = ?, release_note = ?
            WHERE id = ?
            """,
            (
                source_device_id or row["source_device_id"],
                client_id or row["client_id"],
                model or row["model"],
                clean_version,
                clean_battery,
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
                INSERT INTO devices(id, device_code, display_name, online_status, battery_percent, firmware_version, last_online_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    display_name = excluded.display_name,
                    online_status = excluded.online_status,
                    battery_percent = excluded.battery_percent,
                    firmware_version = excluded.firmware_version,
                    last_online_at = excluded.last_online_at
                """,
                (
                    device_id,
                    device.get("device_code") or DEMO_DEVICE_CODE,
                    device.get("display_name") or "我的白泽",
                    device.get("online_status") or "unknown",
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
