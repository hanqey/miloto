

import os
import re
import threading
import time
import ctypes
import itertools

from core.runtime import wxid_to_int, contact_map, paused
from core import runtime
from core.loggingx import build_logger
from net.onebot_proto import build_message_event
from net.file_server import share_file

log = build_logger("miloto.relay")

REPLAY_GRACE = 30

def _as_float(v):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f > 1e12:
        f /= 1000.0
    return f

class InboundRelay:
    def __init__(self, settings, bus, sender):
        self.settings = settings
        self.bus = bus
        self.sender = sender
        self.pushers = []
        self.weflow_clients = []
        self.processed = set()
        self.sent_recently = {}
        self.buffers = {}
        self.lock = threading.Lock()
        self._dl_lock = threading.Lock()
        self.start_ts = int(time.time())

    def _ignore(self, data: dict) -> bool:
        content = data.get("content", "")
        msg_type = data.get("type", 0) or data.get("msgType", 0)
        if data.get("sourceName", "") in self.settings.bot_names:
            return True
        if self.settings.bot_wxid and data.get("talkerId", "") == self.settings.bot_wxid:
            return True
        if msg_type in (34,):
            return True
        if content and ("[语音]" in content or "[表情]" in content):
            return True
        if not content or content.strip() == "":
            return True
        return False

    def on_raw(self, data: dict) -> None:

        rawid = data.get("rawid", "")
        content = data.get("content", "")
        if rawid:
            if rawid in self.processed:
                return
            self.processed.add(rawid)
        else:

            bucket = int(time.time() // 30)
            fk = (data.get("sessionId", ""), data.get("talkerId", ""), content, bucket)
            if fk in self.processed:
                return
            self.processed.add(fk)

        if len(self.processed) > 5000:
            self._prune_processed()

        if self._ignore(data):
            return

        data = self.bus.emit(self.bus.INBOUND, data)
        if data is None:
            return

        msg_ts = _as_float(data.get("timestamp") or data.get("time"))
        data["_replay"] = bool(msg_ts is not None and msg_ts < self.start_ts - REPLAY_GRACE)
        self._enqueue(data)

    def _enqueue(self, data: dict) -> None:
        content = data.get("content", "")
        source = data.get("sourceName", "") or data.get("talkerName", "") or "未知"
        session = data.get("sessionId", "") or source
        group_raw = data.get("groupName", "")
        is_group = (data.get("sessionType", "") == "group") or bool(group_raw) \
            or "@chatroom" in session

        if content.startswith("[图片]"):
            threading.Thread(target=self._handle_image, args=(data,),
                             daemon=True).start()
            return

        if content.startswith("[文件]") or str(data.get("mediaType", "")) == "file" \
                or str(data.get("type", "")) == "file":
            threading.Thread(target=self._handle_file, args=(data,),
                             daemon=True).start()
            return

        if data.get("_replay"):
            return

        now = time.time()
        if content and content in self.sent_recently \
                and now - self.sent_recently[content] < 120:
            log.info(f"自回复去重跳过: {content[:30]}")
            return

        if content:
            self.sent_recently[content] = now
            if len(self.sent_recently) > 500:
                self._prune_sent_recently(now)

        sender_in_group = data.get("senderName", "") or data.get("sender", "") \
            or data.get("sourceName", "")
        if is_group:
            base = re.sub(r'\s*\(\d+\)\s*$', '', group_raw or source).strip()
            contact = base
        else:
            base = source
            contact = source

        if is_group and sender_in_group:
            key = f"{session}_{sender_in_group}"
        else:
            key = session

        with self.lock:
            if key not in self.buffers:
                self.buffers[key] = {
                    "messages": [], "timer": None, "version": 0,
                    "processing": False, "contact": contact, "is_group": is_group,
                    "source": source, "group": base if is_group else "",
                    "sender": sender_in_group if is_group else "",
                    "session": session,
                }
            entry = self.buffers[key]
            entry["messages"].append(content)
            if not entry["processing"]:
                if entry["timer"]:
                    entry["timer"].cancel()
                entry["version"] += 1
                v = entry["version"]
                t = threading.Timer(self.settings.buffer_seconds,
                                    lambda k=key, ver=v: self._flush(k, ver))
                t.daemon = True
                t.start()
                entry["timer"] = t

    def _prune_processed(self) -> None:

        half = len(self.processed) // 2
        self.processed = set(itertools.islice(self.processed, half))

    def _prune_sent_recently(self, now: float) -> None:
        expired = [c for c, t in self.sent_recently.items() if now - t >= 120]
        for c in expired:
            del self.sent_recently[c]

    def _handle_image(self, data: dict) -> None:
        source = data.get("sourceName", "") or "未知"
        group_raw = data.get("groupName", "")
        session = data.get("sessionId", "")
        talker = data.get("talkerId", "") or session
        if "@weclaw" in talker:

            log.info(f"[图片] 会话 {talker} 属 OpenClaw 命名空间，WeFlow 无法解码其图片，跳过图片兜底")
            return
        is_group = bool(group_raw) or "@chatroom" in session
        key = f"{session}_{source}"

        path = None
        if self.settings.auto_open_to_download and not data.get("_replay"):
            log.info(f"[图片] 切会话触发微信下载原图: {source}")
            path = self._trigger_download(source, self._fetch, talker)
        if not path:
            path = self._fetch(talker)
        if not path:
            if data.get("_replay"):

                log.info(f"[图片] 重放历史消息，跳过重试: {source}")
                return

            log.warning(f"[图片] 暂未取到原图，稍后重试: {source}（若长期如此，多为 WeFlow REST 消息库为空，"
                        f"请在 WeFlow 中同步/导入聊天记录、确认微信数据目录与解密密钥配置正确）")
            self._retry_image_later(talker, key, source, group_raw, session, is_group)
            return
        name = os.path.basename(path)
        url = share_file(path, name)
        if not url:
            self._inject(key, source, group_raw, session, is_group, "[图片]（生成分享链接失败）")
            return
        log.info(f"[图片] 分享 URL: {url} (name={name})")
        self._inject(key, source, group_raw, session, is_group,
                     {"type": "image", "url": url, "name": name})

    def _fetch(self, talker: str):

        for wf in self.weflow_clients:
            try:
                p = wf.fetch_image(talker)
                if p:
                    return p
            except Exception as exc:
                log.warning(f"取图失败[{wf.name}]: {exc}")
        return None

    def _encode(self, path: str):
        import base64
        try:
            with open(path, "rb") as f:
                raw = f.read()
            if not raw:
                return None, None
            return base64.b64encode(raw).decode("ascii"), _mime_of(path)
        except Exception:
            return None, None

    def _retry_image_later(self, talker, key, source, group, session, is_group):

        self._pending_retry = getattr(self, "_pending_retry", set())
        if key in self._pending_retry:
            return
        self._pending_retry.add(key)

        def _r():
            self._pending_retry.discard(key)
            if not runtime.running:
                return
            p = self._fetch(talker)
            if not p:
                return
            name = os.path.basename(p)
            url = share_file(p, name)
            if not url:
                return
            self._inject(key, source, group, session, is_group,
                         {"type": "image", "url": url, "name": name})
        t = threading.Timer(25, _r)
        t.daemon = True
        t.start()

    def _resolve_file_local(self, filename: str, msg_ts):

        base = self.settings.wechat_file_dir
        months = []
        if msg_ts:
            try:
                months.append(time.strftime("%Y-%m", time.localtime(float(msg_ts))))
            except Exception:
                pass
        months.append(time.strftime("%Y-%m"))
        months = list(dict.fromkeys(months))

        if base:
            for ym in months:
                cand = os.path.join(base, ym, filename)
                log.info(f"[文件] 尝试本地路径: {cand}")
                if os.path.exists(cand):
                    return cand
            return None

        root = self.settings.wechat_image_dir
        if not root:
            log.warning("[文件] 未配置 wechat.file_dir / wechat.image_dir，无法按路径读取")
            return None
        log.info(f"[文件] 未配置 wechat.file_dir，将在微信文件根递归搜索: {root}")
        for dp, dn, fn in os.walk(root):
            depth = dp[len(root):].count(os.sep)
            if depth > 6:
                dn[:] = []
                continue
            if filename in fn:
                found = os.path.join(dp, filename)
                log.info(f"[文件] 递归命中本地路径: {found}")
                return found
        return None

    def _fetch_file(self, talker: str):

        for wf in self.weflow_clients:
            try:
                p = wf.fetch_file(talker)
                if p:
                    return p
            except Exception as exc:
                log.warning(f"取文件失败[{wf.name}]: {exc}")
        return None

    def _handle_file(self, data: dict) -> None:
        content = data.get("content", "")
        source = data.get("sourceName", "") or "未知"
        group_raw = data.get("groupName", "")
        session = data.get("sessionId", "")
        talker = data.get("talkerId", "") or session
        if "@weclaw" in talker:

            log.info(f"[文件] 会话 {talker} 属 OpenClaw 命名空间，WeFlow 无法解码其文件，跳过文件兜底")
            return
        is_group = bool(group_raw) or "@chatroom" in session
        key = f"{session}_{source}"
        msg_ts = data.get("timestamp") or data.get("time")
        if isinstance(msg_ts, str):
            try:
                msg_ts = float(msg_ts)
            except Exception:
                msg_ts = None

        filename = re.sub(r'^\[文件\]\s*', '', content or '', flags=re.I).strip()
        if not filename:
            filename = (data.get("fileName") or data.get("mediaName") or "").strip()
        log.info(f"[文件] 文件名: {filename!r}")

        if self.settings.auto_open_to_download and not data.get("_replay"):
            log.info(f"[文件] 切会话触发微信下载: {source}")
            self._open_to_trigger(source)

        path = None
        if filename:
            path = self._resolve_file_local(filename, msg_ts)

        if not path:
            log.info(f"[文件] 本地未找到 {filename!r}，回退 WeFlow 取文件")
            path = self._fetch_file(talker)
        if not path:
            if data.get("_replay"):

                log.info(f"[文件] 重放历史消息，跳过重试: {source}")
                return

            log.info(f"[文件] 暂未取到，稍后重试: {source}")
            self._retry_file_later(talker, key, source, group_raw, session, is_group)
            return

        name = os.path.basename(path)
        try:
            size = os.path.getsize(path)
        except Exception:
            size = 0

        if size > 100 * 1024 * 1024:
            log.warning(f"[文件] 过大({size // 1024 // 1024}MB)，跳过转发: {name}")
            return
        url = share_file(path, name)
        if not url:
            log.warning(f"[文件] 文件服务未就绪，稍后重试: {path}")
            self._retry_file_later(talker, key, source, group_raw, session, is_group)
            return
        log.info(f"[文件] 分享 URL: {url} (name={name})")
        self._inject(key, source, group_raw, session, is_group,
                     {"type": "file", "url": url, "name": name})

    def _open_to_trigger(self, display_name):

        if not display_name:
            return
        sender = self.sender
        if not hasattr(sender, "open_chat"):
            return
        prev_hwnd = None
        try:
            prev_hwnd = ctypes.windll.user32.GetForegroundWindow()
        except Exception:
            prev_hwnd = None
        with self._dl_lock:
            try:
                if not sender.open_chat(display_name):
                    log.warning(f"[自动下载] 切到会话失败: {display_name}")
                    return
            except Exception as exc:
                log.error(f"[自动下载] 切会话异常: {exc}")
                return

            time.sleep(5)
        if prev_hwnd:
            try:
                ctypes.windll.user32.SetForegroundWindow(prev_hwnd)
            except Exception:
                pass

    def _trigger_download(self, display_name, fetch_func, talker):

        if not display_name:
            return None
        sender = self.sender
        if not hasattr(sender, "open_chat"):
            return None
        prev_hwnd = None
        try:
            prev_hwnd = ctypes.windll.user32.GetForegroundWindow()
        except Exception:
            prev_hwnd = None
        with self._dl_lock:
            try:
                if not sender.open_chat(display_name):
                    log.warning(f"[自动下载] 切到会话失败: {display_name}")
                    return None
            except Exception as exc:
                log.error(f"[自动下载] 切会话异常: {exc}")
                return None

            time.sleep(5)
            try:
                path = fetch_func(talker)
            except Exception:
                path = None

        if prev_hwnd:
            try:
                ctypes.windll.user32.SetForegroundWindow(prev_hwnd)
            except Exception:
                pass
        return path

    def _retry_file_later(self, talker, key, source, group, session, is_group):

        self._pending_retry = getattr(self, "_pending_retry", set())
        if key in self._pending_retry:
            return
        self._pending_retry.add(key)

        def _r():
            self._pending_retry.discard(key)
            if not runtime.running:
                return
            p = self._fetch_file(talker)
            if not p:
                return
            name = os.path.basename(p)
            try:
                size = os.path.getsize(p)
            except Exception:
                size = 0
            if size > 100 * 1024 * 1024:
                return
            url = share_file(p, name)
            if not url:
                return
            self._inject(key, source, group, session, is_group,
                         {"type": "file", "url": url, "name": name})
        t = threading.Timer(25, _r)
        t.daemon = True
        t.start()

    def _inject(self, key, source, group, session, is_group, payload) -> None:

        if isinstance(payload, dict) and payload.get("type") in ("image", "file"):
            self.bus.emit(self.bus.MEDIA_READY, {
                "kind": payload["type"],
                "url": payload.get("url"),
                "name": payload.get("name"),
                "contact": group if (is_group and group) else source,
                "session": session,
                "is_group": is_group,
            })
        with self.lock:
            if key not in self.buffers:
                self.buffers[key] = {
                    "messages": [],
                    "timer": None, "version": 0, "processing": False,
                    "contact": group if (is_group and group) else source,
                    "is_group": is_group, "source": source,
                    "group": group if is_group else "", "sender": "", "session": session,
                }

            self.buffers[key]["messages"].append(payload)
            self._arm(key, 2)

    def _arm(self, key, delay) -> None:
        entry = self.buffers[key]
        if entry["timer"]:
            entry["timer"].cancel()
        entry["version"] += 1
        v = entry["version"]
        t = threading.Timer(delay, lambda k=key, ver=v: self._flush(k, ver))
        t.daemon = True
        t.start()
        entry["timer"] = t

    def _push(self, event: dict) -> bool:

        ok = False
        for p in self.pushers:
            try:
                if p.push(event):
                    ok = True
            except Exception as exc:
                log.warning(f"推送失败: {exc}")
        return ok

    def _flush(self, key: str, version: int) -> None:
        if runtime.paused.is_set():
            t = threading.Timer(1.0, lambda: self._flush(key, version))
            t.daemon = True
            t.start()
            return
        with self.lock:
            if key not in self.buffers:
                return
            entry = self.buffers[key]
            if version != entry["version"]:
                return
            if not entry["messages"]:
                return
            msgs = list(entry["messages"])
            entry["messages"] = []
            entry["processing"] = True

        contact = entry["contact"]
        is_group = entry["is_group"]
        texts, imgs, files = [], [], []
        for m in msgs:
            if isinstance(m, dict) and m.get("type") == "image":
                imgs.append(m)
            elif isinstance(m, dict) and m.get("type") == "file":
                files.append(m)
            else:
                texts.append(m if isinstance(m, str) else str(m))
        combined = "\n".join(texts)

        if is_group:
            gid = wxid_to_int(entry["group"] or contact)
            sname = entry["sender"] or entry["source"] or "未知"
            uid = wxid_to_int(f"{entry['group']}_{sname}")

            clean = combined
            segs = []
            for nick in self.settings.bot_names:
                if f"@{nick}" in clean:

                    segs.append({"type": "at", "data": {"qq": int(runtime.self_id)}})
                    clean = clean.replace(f"@{nick}", "").strip()
            if clean:
                segs.append({"type": "text", "data": {"text": clean}})
            for im in imgs:
                segs.append({"type": "image", "data": {"file": im['url']}})
            for fl in files:
                segs.append({"type": "file", "data": {"url": fl["url"],
                                                       "name": fl.get("name", "file")}})
            event = build_message_event("group", uid, segs, group_id=gid,
                                        group_name=entry["group"] or contact, nickname=sname)
            contact_map[gid] = contact
        else:
            sname = entry["source"] or contact
            segs = []
            if combined:
                segs.append({"type": "text", "data": {"text": combined}})
            for im in imgs:
                segs.append({"type": "image", "data": {"file": im['url']}})
            for fl in files:
                segs.append({"type": "file", "data": {"url": fl["url"],
                                                       "name": fl.get("name", "file")}})
            event = build_message_event("private", wxid_to_int(contact), segs, nickname=sname)
            contact_map[wxid_to_int(contact)] = contact

        event = self.bus.emit(self.bus.PRE_PUSH, event)
        if event is None:
            log.info(f"插件拦截推送 [{contact}]")
            with self.lock:
                if key in self.buffers:
                    self.buffers[key]["processing"] = False
            return

        if self._push(event):
            log.info(f"已推送 [{contact}] {len(texts)}文本+{len(imgs)}图片+{len(files)}文件")
        else:
            log.warning(f"无 AstrBot 在线 [{contact}]")

        self.bus.emit(self.bus.POST_PUSH, event)

        with self.lock:
            if key in self.buffers:
                self.buffers[key]["processing"] = False

def _mime_of(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    return {"": "image/jpeg", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp"}.get(ext, "image/jpeg")
