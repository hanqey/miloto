

import asyncio
import base64
import os

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
                    await asyncio.to_thread(self.sender.send_text, contact, text)
            elif seg_type == "image":
                await self._send_image(contact, seg_data.get("file", ""))
            elif seg_type == "face":
                await asyncio.to_thread(self.sender.send_text, contact, "[表情]")
            elif seg_type == "record":
                await self._send_voice(contact, seg_data.get("file", ""))

    async def _send_image(self, contact: str, file_val: str) -> None:
        path = self._resolve_file(file_val, decode_base64_image)
        if not path:
            return
        try:
            await asyncio.to_thread(self.sender.send_image, contact, path)
        finally:
            if path and "tmp" in path:
                try:
                    os.unlink(path)
                except Exception:
                    pass

    async def _send_voice(self, contact: str, file_val: str) -> None:
        path = self._resolve_file(file_val, decode_base64_audio)
        if not path:
            return
        try:
            await asyncio.to_thread(self.sender.send_voice, contact, path)
        finally:
            if path and "tmp" in path:
                try:
                    os.unlink(path)
                except Exception:
                    pass

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
