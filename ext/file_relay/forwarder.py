

import os

from core.runtime import wxid_to_int, contact_map
from net.onebot_proto import build_message_event

def push_file(pusher, settings, contact: str, path: str) -> bool:

    if not pusher or not path or not os.path.isfile(path):
        return False

    is_group = "@chatroom" in (contact or "")
    base_name = os.path.basename(path)

    segs = [
        {"type": "file", "data": {"file": path, "name": base_name}},
        {"type": "text", "data": {"text": f"[文件] {base_name}"}},
    ]

    if is_group:

        from core import runtime
        gid = wxid_to_int(contact)
        uid = gid
        at_seg = [{"type": "at", "data": {"qq": int(runtime.self_id)}}]
        event = build_message_event(
            "group", uid, at_seg + segs, group_id=gid,
            group_name=contact, nickname=contact,
        )
        contact_map[gid] = contact
    else:
        uid = wxid_to_int(contact)
        event = build_message_event("private", uid, segs, nickname=contact)
        contact_map[uid] = contact

    ok = pusher.push(event)
    return ok
