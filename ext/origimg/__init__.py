

import os
import time

from core.extension import Extension, Context
from core.loggingx import build_logger
from core.runtime import wxid_to_int, contact_map
from net.onebot_proto import build_message_event
from .grabber import Grabber

log = build_logger("miloto.ext.origimg")

class OrigImg(Extension):
    name = "origimg"

    def on_load(self, ctx: Context) -> None:
        self.ctx = ctx

        self.vision_url = ctx.settings.vision_url
        self.vision_key = ctx.settings.vision_key
        self.vision_model = ctx.settings.vision_model
        self.image_dir = ctx.settings.wechat_image_dir
        self.auto_original = ctx.settings.auto_original
        self.last_image = {}
        self.lock = __import__("threading").Lock()
        if self.vision_url and self.image_dir:
            self.grabber = Grabber(ctx, self)
            log.info("原图转发已启用")
        else:
            self.grabber = None
            log.warning("未配置 vision.url / wechat.image_dir，原图转发未启用")

    def on_inbound(self, msg):

        if msg.get("content") != "[图片]":
            return msg
        contact = msg.get("sourceName", "") or msg.get("talkerName", "") or "未知"
        talker = msg.get("talkerId", "") or msg.get("sessionId", "")
        with self.lock:
            self.last_image[contact] = {"talker": talker, "ts": time.time()}
        return msg

    def request_original(self, contact: str) -> bool:

        if not self.grabber:
            log.warning("原图转发未启用，无法处理")
            return False
        with self.lock:
            info = self.last_image.get(contact)
        talker = info.get("talker", "") if info else ""
        path = self.grabber.capture_original(contact, talker)
        if not path:
            self._push_text(contact, "（无法获取原图，请确认微信已打开该聊天且点开了图片）")
            return False
        return self._push_image(contact, path)

    def _push_image(self, contact: str, path: str) -> bool:

        import base64
        try:
            with open(path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("ascii")
        except Exception:
            return False
        mime = _mime_of(path)
        is_group = "@chatroom" in (contact or "")
        if is_group:
            from core import runtime
            gid = wxid_to_int(contact)
            segs = [{"type": "image", "data": {"file": f"base64://{b64}"}}]
            event = build_message_event("group", gid, segs, group_id=gid,
                                        group_name=contact, nickname=contact)
            contact_map[gid] = contact
        else:
            uid = wxid_to_int(contact)
            segs = [{"type": "image", "data": {"file": f"base64://{b64}"}}]
            event = build_message_event("private", uid, segs, nickname=contact)
            contact_map[uid] = contact
        return self.ctx.pusher.push(event)

    def _push_text(self, contact: str, text: str) -> bool:

        is_group = "@chatroom" in (contact or "")
        if is_group:
            from core import runtime
            gid = wxid_to_int(contact)
            event = build_message_event("group", gid,
                                        [{"type": "text", "data": {"text": text}}],
                                        group_id=gid, group_name=contact, nickname=contact)
            contact_map[gid] = contact
        else:
            uid = wxid_to_int(contact)
            event = build_message_event("private", uid,
                                        [{"type": "text", "data": {"text": text}}],
                                        nickname=contact)
            contact_map[uid] = contact
        return self.ctx.pusher.push(event)

    def on_file(self, payload) -> None:

        path, meta = payload
        if not self.grabber:
            return
        if not meta.get("is_original"):
            return
        contact = meta.get("contact", "")
        if contact:
            self._push_image(contact, path)

def _mime_of(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    return {"": "image/jpeg", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp"}.get(ext, "image/jpeg")
