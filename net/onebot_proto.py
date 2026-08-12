

import base64
import os
import tempfile
import time

from core import runtime
from core.loggingx import build_logger

log = build_logger("miloto.ob11")

def build_api_response(action: str) -> dict:

    if action == "get_version_info":
        return {
            "app_name": "miloto",
            "app_version": "1.0.0",
            "protocol_version": "v11",
            "version": "1.0.0",
        }
    if action == "get_login_info":
        return {"user_id": runtime.self_id, "nickname": "miloto"}
    if action == "get_status":
        return {"online": True, "good": True, "stat": {"packet_received": 0, "packet_sent": 0}}
    if action in ("can_send_image", "can_send_record"):
        return {"yes": True}
    if action == "get_friend_list":
        return []
    if action == "get_group_list":
        return []
    if action in ("set_restart", "clean_cache"):
        return {}
    return {}

def build_message_event(message_type: str, user_id: int, message: list,
                        group_id: int = 0, group_name: str = "",
                        nickname: str = "") -> dict:

    event = {
        "time": int(time.time()),
        "self_id": runtime.self_id,
        "post_type": "message",
        "message_type": message_type,
        "user_id": user_id,
        "message": message,
        "raw_message": "".join(
            seg.get("data", {}).get("text", "") for seg in message
            if seg.get("type") == "text"
        ),
        "sender": {"user_id": user_id, "nickname": nickname or str(user_id)},
    }
    if message_type == "group":
        event["group_id"] = group_id
        event["group_name"] = group_name or str(group_id)
        event["sub_type"] = "normal"
    return event

def decode_base64_image(b64_data: str) -> str | None:

    try:
        raw = base64.b64decode(b64_data)
        tmp_file = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        tmp_file.write(raw)
        tmp_file.close()
        return tmp_file.name
    except Exception as exc:
        log.warning(f"base64 图片解码失败: {exc}")
        return None

def decode_base64_audio(b64_data: str) -> str | None:

    try:
        raw = base64.b64decode(b64_data)

        if raw[:4] == b"#!SILK":
            suffix = ".sil"
        elif raw[:3] == b"AMR":
            suffix = ".amr"
        elif raw[:2] == b"ID3" or raw[:4] == b"\xff\xfb" or raw[:4] == b"\xff\xf3":
            suffix = ".mp3"
        elif raw[:4] == b"RIFF":
            suffix = ".wav"
        else:
            suffix = ".mp3"
        tmp_file = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        tmp_file.write(raw)
        tmp_file.close()
        return tmp_file.name
    except Exception as exc:
        log.warning(f"base64 语音解码失败: {exc}")
        return None
