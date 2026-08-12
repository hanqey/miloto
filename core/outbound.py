

import base64

from core.runtime import contact_map
from core.loggingx import build_logger
from net.onebot_proto import decode_base64_image, decode_base64_audio

log = build_logger("miloto.outbound")

class OutboundHandler:
    def __init__(self, settings, sender):
        self.settings = settings
        self.sender = sender

    async def send_only(self, action: str, params: dict) -> None:

        is_group = action == "send_group_msg"
        target = params.get("group_id" if is_group else "user_id", 0)
        message = params.get("message", [])
        contact = contact_map.get(target, str(target))

        for seg in message:
            if not isinstance(seg, dict):
                continue
            seg_type = seg.get("type", "")
            seg_data = seg.get("data", {})
            if seg_type == "text":
                text = seg_data.get("text", "")
                if text:
                    self.sender.enqueue("text", contact, text)
            elif seg_type == "image":
                self._enqueue_file(contact, seg_data.get("file", ""), "image")
            elif seg_type == "face":
                self.sender.enqueue("text", contact, "[表情]")
            elif seg_type == "record":
                self._enqueue_file(contact, seg_data.get("file", ""), "voice")

    def _enqueue_file(self, contact: str, file_val: str, kind: str) -> None:

        path = self._resolve_file(file_val, decode_base64_image if kind == "image" else decode_base64_audio)
        if not path:
            return
        self.sender.enqueue(kind, contact, path)

    @staticmethod
    def _resolve_file(file_val: str, decode_fn):

        if not file_val:
            return None
        if file_val.startswith("base64://"):
            try:
                return decode_fn(file_val)
            except Exception as exc:
                log.warning(f"base64 解码失败: {exc}")
                return None
        return file_val
