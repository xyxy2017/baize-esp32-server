import json
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict


DEMO_TOKEN = "demo-token"
DEMO_USER_ID = "demo_user"
DEMO_DEVICE_CODE = "123456"
DEMO_DEVICE_ID = "baize_dev_001"
EMOTION_PREFIX_PATTERN = re.compile(r"^[\s]*(?:[😶🙂😆😂😔😠😭😍😳😲😱🤔😉😎😌🤤😘😏😴😜🙄]\s*)+")
ACTION_PARENTHETICAL_PATTERN = re.compile(r"[（(][^（）()\[\]【】\n]{1,80}[）)]")
ACTION_BRACKET_PATTERN = re.compile(r"[\[【][^\[\]【】（）()\n]{1,80}[\]】]")
BROKEN_LEADING_ACTION_PATTERN = re.compile(
    r"^\s*[^，。！？!?；;：:\n]{1,40}[）)\]】]\s*"
)
EMOJI_EMOTION_MAP = {
    "😶": "neutral",
    "🙂": "happy",
    "😆": "laughing",
    "😂": "funny",
    "😔": "sad",
    "😠": "angry",
    "😭": "crying",
    "😍": "loving",
    "😳": "embarrassed",
    "😲": "surprised",
    "😱": "shocked",
    "🤔": "thinking",
    "😉": "winking",
    "😎": "cool",
    "😌": "relaxed",
    "🤤": "delicious",
    "😘": "loving",
    "😏": "confident",
    "😴": "sleepy",
    "😜": "silly",
    "🙄": "confused",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def state_path_from_config(config: dict) -> str:
    state_path = config.get("app_demo", {}).get("state_path")
    if state_path:
        return state_path
    return os.path.join(os.getcwd(), "data", "app_demo_state.json")


def default_state() -> Dict[str, Any]:
    created_at = now_iso()
    return {
        "users": {
            DEMO_USER_ID: {
                "id": DEMO_USER_ID,
                "display_name": "Demo User",
                "created_at": created_at,
            }
        },
        "bindings": {DEMO_USER_ID: []},
        "devices": {
            DEMO_DEVICE_ID: {
                "id": DEMO_DEVICE_ID,
                "device_code": DEMO_DEVICE_CODE,
                "display_name": "我的白泽",
                "online_status": "unknown",
                "battery_percent": None,
                "firmware_version": "0.1.0-demo",
                "last_online_at": None,
                "settings": {
                    "device_id": DEMO_DEVICE_ID,
                    "baize_nickname": "白泽",
                    "user_call_name": "小伙伴",
                    "personality_mode": "curious",
                },
                "memories": [],
                "dialogues": [],
                "diaries": [],
                "ota": {
                    "current_version": "0.1.0-demo",
                    "latest_version": "0.1.0-demo",
                    "update_available": False,
                    "release_note": "等待设备版本上报",
                },
            }
        },
    }


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
    cleaned = re.sub(r"\s+([，。！？!?；;：:])", r"\1", cleaned)
    return cleaned


def infer_emotion(text: str, fallback: str = "neutral") -> str:
    stripped = (text or "").strip()
    if not stripped:
        return fallback
    for char in stripped:
        if char.isspace():
            continue
        return EMOJI_EMOTION_MAP.get(char, fallback)
    return fallback


def merge_defaults(state: Dict[str, Any]) -> Dict[str, Any]:
    defaults = default_state()
    for key, value in defaults.items():
        state.setdefault(key, value)
    state["users"].setdefault(DEMO_USER_ID, defaults["users"][DEMO_USER_ID])
    state["devices"].setdefault(DEMO_DEVICE_ID, defaults["devices"][DEMO_DEVICE_ID])
    state["bindings"].setdefault(DEMO_USER_ID, [])
    device = state["devices"][DEMO_DEVICE_ID]
    if device.get("battery_percent") == 86 and not device.get("battery_reported_at"):
        device["battery_percent"] = None
    ota = device.setdefault("ota", defaults["devices"][DEMO_DEVICE_ID]["ota"])
    if ota.get("release_note") == "Demo version":
        firmware_version = device.get("firmware_version")
        if firmware_version and firmware_version != "0.1.0-demo":
            ota["release_note"] = f"设备当前版本 {firmware_version}"
        else:
            ota["release_note"] = "等待设备版本上报"
    device["memories"] = [
        item for item in device.get("memories", []) if item.get("id") != "mem_001"
    ]
    device.setdefault("diaries", [])
    device["dialogues"] = [
        item
        for item in device.get("dialogues", [])
        if item.get("id") != "dlg_001" and not is_legacy_xiaozhi_dialogue(item)
    ]
    for item in device["dialogues"]:
        item["baize_text"] = clean_baize_text(item.get("baize_text", ""))
    return state


def save_state(path: str, state: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as file:
        json.dump(state, file, ensure_ascii=False, indent=2)


def load_state(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        state = default_state()
        save_state(path, state)
        return state
    with open(path, "r", encoding="utf-8") as file:
        return merge_defaults(json.load(file))


def append_dialogue(
    config: dict,
    source_device_id: str,
    session_id: str,
    user_text: str,
    baize_text: str,
    emotion: str = "neutral",
) -> Dict[str, Any]:
    user_text = (user_text or "").strip()
    inferred_emotion = infer_emotion(baize_text, emotion or "neutral")
    baize_text = clean_baize_text(baize_text)
    if not user_text or not baize_text:
        return {}

    path = state_path_from_config(config)
    state = load_state(path)
    device = state["devices"].setdefault(
        DEMO_DEVICE_ID, default_state()["devices"][DEMO_DEVICE_ID]
    )
    dialogues = device.setdefault("dialogues", [])
    created_at = now_iso()
    item = {
        "id": f"dlg_{uuid.uuid4().hex}",
        "source_device_id": source_device_id,
        "session_id": session_id,
        "user_text": user_text,
        "baize_text": baize_text,
        "emotion": inferred_emotion,
        "created_at": created_at,
    }
    dialogues.insert(0, item)
    del dialogues[100:]
    device["online_status"] = "online"
    device["last_online_at"] = created_at
    save_state(path, state)
    return item


def _date_from_iso(value: str) -> str:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date().isoformat()
    except Exception:
        return now_iso()[:10]


def _dialogues_for_date(device: Dict[str, Any], diary_date: str) -> list[Dict[str, Any]]:
    dialogues = [
        item
        for item in device.get("dialogues", [])
        if _date_from_iso(str(item.get("created_at", ""))) == diary_date
    ]
    return list(reversed(dialogues))


def _primary_emotion(dialogues: list[Dict[str, Any]]) -> str:
    for item in reversed(dialogues):
        emotion = str(item.get("emotion") or "").strip()
        if emotion and emotion != "neutral":
            return emotion
    return str(dialogues[-1].get("emotion") or "neutral") if dialogues else "neutral"


def generate_diary(config: dict, diary_date: str | None = None) -> Dict[str, Any]:
    path = state_path_from_config(config)
    state = load_state(path)
    device = state["devices"].setdefault(
        DEMO_DEVICE_ID, default_state()["devices"][DEMO_DEVICE_ID]
    )
    if diary_date is None:
        latest_dialogue = next(iter(device.get("dialogues", [])), {})
        diary_date = _date_from_iso(str(latest_dialogue.get("created_at", now_iso())))

    dialogues = _dialogues_for_date(device, diary_date)
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
    existing = next(
        (item for item in device.setdefault("diaries", []) if item.get("date") == diary_date),
        None,
    )
    diary = {
        "id": existing.get("id") if existing else f"diary_{uuid.uuid4().hex}",
        "date": diary_date,
        "title": f"{diary_date} 的白泽小记",
        "summary": summary,
        "primary_emotion": primary_emotion,
        "dialogue_count": len(dialogues),
        "quotes": quotes,
        "baize_note": "今天也有好好聊过啦，小伙伴。",
        "generated_at": generated_at,
    }
    diaries = device.setdefault("diaries", [])
    if existing:
        existing.update(diary)
    else:
        diaries.insert(0, diary)
    del diaries[30:]
    save_state(path, state)
    return diary


def update_device_report(
    config: dict,
    source_device_id: str,
    client_id: str = "",
    model: str = "",
    firmware_version: str = "",
    battery_percent: int | None = None,
) -> Dict[str, Any]:
    path = state_path_from_config(config)
    state = load_state(path)
    device = state["devices"].setdefault(
        DEMO_DEVICE_ID, default_state()["devices"][DEMO_DEVICE_ID]
    )
    reported_at = now_iso()

    if source_device_id:
        device["source_device_id"] = source_device_id
    if client_id:
        device["client_id"] = client_id
    if model:
        device["model"] = model
    if firmware_version:
        device["firmware_version"] = firmware_version
        ota = device.setdefault("ota", {})
        ota["current_version"] = firmware_version
        ota.setdefault("latest_version", firmware_version)
        if not ota.get("update_available", False):
            ota["latest_version"] = firmware_version
        ota["release_note"] = f"设备当前版本 {firmware_version}"
    if battery_percent is not None:
        device["battery_percent"] = max(0, min(100, int(battery_percent)))
        device["battery_reported_at"] = reported_at

    device["online_status"] = "online"
    device["last_online_at"] = reported_at
    save_state(path, state)
    return device


def update_ota_report(
    config: dict,
    current_version: str,
    latest_version: str,
    update_available: bool,
    release_note: str,
) -> Dict[str, Any]:
    path = state_path_from_config(config)
    state = load_state(path)
    device = state["devices"].setdefault(
        DEMO_DEVICE_ID, default_state()["devices"][DEMO_DEVICE_ID]
    )
    ota = device.setdefault("ota", {})
    if current_version:
        ota["current_version"] = current_version
        device["firmware_version"] = current_version
    if latest_version:
        ota["latest_version"] = latest_version
    ota["update_available"] = bool(update_available)
    if release_note:
        ota["release_note"] = release_note
    save_state(path, state)
    return ota


def prompt_context_for_device(config: dict, device_identifier: str) -> str:
    if not device_identifier:
        return ""
    try:
        state = load_state(state_path_from_config(config))
    except Exception:
        return ""

    device = None
    for candidate in state.get("devices", {}).values():
        if device_identifier in (
            candidate.get("id"),
            candidate.get("source_device_id"),
            candidate.get("client_id"),
        ):
            device = candidate
            break
    if not device:
        return ""

    settings = device.get("settings") or {}
    baize_nickname = str(settings.get("baize_nickname") or "").strip()
    user_call_name = str(settings.get("user_call_name") or "").strip()
    personality_mode = str(settings.get("personality_mode") or "").strip()
    if not any((baize_nickname, user_call_name, personality_mode)):
        return ""

    lines = ["", "<app_device_settings>"]
    if baize_nickname:
        lines.append(f"白泽昵称：{baize_nickname}")
    if user_call_name:
        lines.append(f"用户称呼：{user_call_name}")
    if personality_mode:
        lines.append(f"性格模式：{personality_mode}")
    lines.append("对话时优先遵守这些 App 设备设置，但不得违背白泽幼灵基础人格。")
    lines.append("</app_device_settings>")
    return "\n".join(lines)
