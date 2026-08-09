

import json
import os
import time
import requests

from core import runtime
from core.loggingx import build_logger

log = build_logger("miloto.weflow")

class WeFlowClient:
    def __init__(self, name: str, base_url: str, token: str, attachments: str,
                 file_storage_dir: str = "", reconnect: int = 10):
        self.name = name
        self.base = base_url.rstrip("/")
        self.token = token
        self.attach = attachments
        self.file_storage_dir = file_storage_dir
        self.reconnect = max(3, min(600, int(reconnect)))
        self._active = True
        self._connected = False
        self._thread = None

    def stop(self) -> None:

        self._active = False

    def is_connected(self) -> bool:
        return self._connected

    def listen(self, on_message) -> None:

        url = f"{self.base}/api/v1/push/messages?access_token={self.token}"
        headers = {"Accept": "text/event-stream", "Cache-Control": "no-cache"}
        while self._active:
            try:
                resp = requests.get(url, headers=headers, stream=True, timeout=None)
                if resp.status_code != 200:
                    log.error(f"[{self.name}] WeFlow SSE 连接失败: HTTP {resp.status_code}")
                    self._connected = False
                    if self._active:
                        log.info(f"[{self.name}] {self.reconnect}s 后重试连接 WeFlow")
                        time.sleep(self.reconnect)
                    continue
                log.info(f"[{self.name}] 已连接 WeFlow 推送（连接开启）")
                self._connected = True
                for line in resp.iter_lines(decode_unicode=True):
                    if not self._active:

                        break
                    if not runtime.running:

                        continue
                    if not line or not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if not payload:
                        continue
                    try:
                        data = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    on_message(data)

                if self._connected:
                    log.info(f"[{self.name}] WeFlow 连接已关闭（SSE 流结束）")
                self._connected = False
                if self._active:
                    log.info(f"[{self.name}] {self.reconnect}s 后重连 WeFlow")
                    time.sleep(self.reconnect)
            except requests.exceptions.ConnectionError:
                log.error(f"[{self.name}] 无法连接 WeFlow（连接断开）")
                self._connected = False
                if self._active:
                    log.info(f"[{self.name}] {self.reconnect}s 后重试连接 WeFlow")
                    time.sleep(self.reconnect)
            except Exception as exc:
                log.error(f"[{self.name}] WeFlow SSE 异常: {exc}")
                self._connected = False
                if self._active:
                    log.info(f"[{self.name}] {self.reconnect}s 后重试连接 WeFlow")
                    time.sleep(self.reconnect)

        log.info(f"[{self.name}] WeFlow 监听已停止")

    def _normalize_url(self, media_url: str, base: str) -> str:

        if not (media_url.startswith("http://") or media_url.startswith("https://")):
            return f"{base.rstrip('/')}/{media_url.lstrip('/')}"
        try:
            from urllib.parse import urlparse, urlunparse
            p = urlparse(media_url)
            if p.hostname in ("127.0.0.1", "localhost"):
                b = urlparse(base)
                return urlunparse(p._replace(scheme=b.scheme, netloc=b.netloc))
        except Exception:
            pass
        return media_url

    @staticmethod
    def _uniq_name(save_dir: str, name: str) -> str:

        cand = name
        i = 2
        while os.path.exists(os.path.join(save_dir, cand)):
            root, ext = os.path.splitext(name)
            cand = f"{root}_{i}{ext}"
            i += 1
        return cand

    def _download(self, media_url: str) -> str | None:

        try:
            media_url = self._normalize_url(media_url, self.base)
            sep = "&" if "?" in media_url else "?"
            dl = f"{media_url}{sep}access_token={self.token}"
            r = requests.get(dl, timeout=30)
            if r.status_code != 200:
                log.warning(f"[{self.name}] WeFlow 图片下载失败: HTTP {r.status_code}")
                return None

            ct = r.headers.get("Content-Type", "")
            ext = ".jpg"
            if "png" in ct:
                ext = ".png"
            elif "gif" in ct:
                ext = ".gif"
            elif "webp" in ct:
                ext = ".webp"
            if ext == ".jpg":
                head = r.content[:12]
                if head[:8] == b"\x89PNG\r\n\x1a\n":
                    ext = ".png"
                elif head[:4] == b"GIF8":
                    ext = ".gif"
                elif head[:4] == b"RIFF" and r.content[8:12] == b"WEBP":
                    ext = ".webp"
            save_dir = os.path.join(self.attach or ".", "wechat_images")
            os.makedirs(save_dir, exist_ok=True)
            path = os.path.join(save_dir, f"wechat_{int(time.time()*1000)}{ext}")
            with open(path, "wb") as f:
                f.write(r.content)
            return path
        except Exception as exc:
            log.error(f"[{self.name}] 下载图片异常: {exc}")
            return None

    def _talker_variants(self, talker: str) -> list:

        variants = []
        seen = set()
        def _add(t):
            if t and t not in seen:
                seen.add(t)
                variants.append(t)
        _add(talker)
        if "@" in talker:
            _add(talker.split("@", 1)[0])
        else:
            _add(talker + "@openim")
        return variants

    def fetch_image(self, talker: str) -> str | None:

        candidates = self._talker_variants(talker)
        params = {"access_token": self.token, "media": "1", "image": "1", "limit": 5}
        MAX_RETRY = 4
        server_err_logged = False
        last_diag = ""
        got_img_msg = False
        for attempt in range(MAX_RETRY):
            messages: list = []
            img_msgs: list = []
            any_200 = False
            for tk in candidates:
                params["talker"] = tk
                try:
                    r = requests.get(f"{self.base}/api/v1/messages", params=params, timeout=10)
                except requests.exceptions.RequestException as exc:
                    log.warning(f"[{self.name}] WeFlow 消息API 网络异常 (talker={tk}): {exc}")
                    continue
                if r.status_code != 200:

                    if not server_err_logged:
                        log.error(f"[{self.name}] WeFlow 消息API HTTP {r.status_code} (talker={tk})："
                                  f"多为 WeFlow 未找到该会话消息数据库（请检查 WeFlow 数据目录 / 微信是否登录）")
                        server_err_logged = True
                    continue
                any_200 = True
                try:
                    data = r.json()
                except Exception:
                    data = []
                messages = data if isinstance(data, list) else data.get("messages", data.get("data", []))
                if not isinstance(messages, list):
                    messages = []
                img_msgs = [m for m in messages if (
                    m.get("mediaType") == "image" or m.get("type") == "image"
                    or m.get("msgType") == "image"
                    or (m.get("content") or "").startswith("[图片]")
                    or bool(m.get("mediaUrl") or m.get("mediaLocalPath"))
                )]
                for m in img_msgs:
                    got_img_msg = True
                    url = (m.get("mediaUrl") or m.get("media_url") or m.get("url")
                           or m.get("imageUrl") or m.get("picUrl"))
                    if url:
                        path = self._download(url)
                        if path:
                            return path
                    local = m.get("mediaLocalPath")
                    if local and os.path.exists(local):
                        return local

            last_diag = (f"会话消息 {len(messages)} 条 / 图片消息 {len(img_msgs)} 条"
                         f" | 均无可下载 mediaUrl/localPath (talker={talker}, 候选={candidates})")
            if attempt < MAX_RETRY - 1:
                time.sleep(3)
        if got_img_msg:
            log.warning(f"[{self.name}] {last_diag}")
        elif any_200:
            log.warning(f"[{self.name}] 消息列表未查到任何图片消息 (talker={talker})")
        else:
            log.warning(f"[{self.name}] 所有 talker 变体均未能从 WeFlow 取到图片 (talker={talker})")
        return None

    def fetch_file(self, talker: str) -> str | None:

        candidates = self._talker_variants(talker)
        params = {"access_token": self.token, "media": "1", "limit": 5}
        MAX_RETRY = 4
        server_err_logged = False
        any_200 = False
        for attempt in range(MAX_RETRY):
            for tk in candidates:
                params["talker"] = tk
                try:
                    r = requests.get(f"{self.base}/api/v1/messages", params=params, timeout=10)
                except requests.exceptions.RequestException as exc:
                    log.warning(f"[{self.name}] WeFlow 消息API 网络异常 (尝试 {attempt+1}/{MAX_RETRY}, talker={tk}): {exc}")
                    continue
                if r.status_code != 200:
                    if not server_err_logged:
                        log.error(f"[{self.name}] WeFlow 消息API HTTP {r.status_code} (talker={tk})："
                                  f"多为 WeFlow 未找到该会话消息数据库（请检查 WeFlow 数据目录 / 微信是否登录）")
                        server_err_logged = True
                    continue
                any_200 = True
                data = r.json()
                messages = data if isinstance(data, list) else data.get("messages", data.get("data", []))
                if not isinstance(messages, list):
                    messages = []
                for msg in messages:

                    if (msg.get("mediaType") == "image" or msg.get("type") == "image"
                            or (msg.get("content") or "").startswith("[图片]")):
                        continue
                    is_file = (
                        msg.get("mediaType") == "file"
                        or msg.get("type") == "file"
                        or (msg.get("content") or "").startswith("[文件]")
                        or bool(msg.get("mediaUrl") or msg.get("mediaLocalPath"))
                    )
                    if not is_file:
                        continue
                    url = (msg.get("mediaUrl") or msg.get("media_url") or msg.get("url")
                           or msg.get("fileUrl") or msg.get("file_url"))
                    name = msg.get("fileName") or msg.get("name")
                    if url:
                        path = self._download_file(url, name)
                        if path:
                            return path
                    local = msg.get("mediaLocalPath")
                    if local and os.path.exists(local):
                        return local
            if attempt < MAX_RETRY - 1:
                time.sleep(3)
        if any_200:
            log.warning(f"[{self.name}] 消息列表无文件 mediaUrl (talker={talker})")
        else:
            log.warning(f"[{self.name}] 所有 talker 变体均未能从 WeFlow 取到文件 (talker={talker})")
        return None

    def _download_file(self, media_url: str, suggested_name: str = None) -> str | None:

        try:
            import re as _re
            media_url = self._normalize_url(media_url, self.base)
            sep = "&" if "?" in media_url else "?"
            dl = f"{media_url}{sep}access_token={self.token}"
            r = requests.get(dl, timeout=60)
            if r.status_code != 200:
                log.warning(f"[{self.name}] WeFlow 文件下载失败: HTTP {r.status_code}")
                return None

            ext = ""
            cd = r.headers.get("Content-Disposition", "")
            if cd:
                m = _re.search(r"filename\*?=(?:UTF-8'')?[\"]?([^\";]+)", cd, _re.I)
                if m:
                    fn = m.group(1).strip().strip('"')
                    ext = os.path.splitext(fn)[1].lower()
                    suggested_name = fn
            if not ext:
                from urllib.parse import urlparse
                ext = os.path.splitext(urlparse(media_url).path)[1].lower()
            if not ext:
                ct = r.headers.get("Content-Type", "")
                if "pdf" in ct:
                    ext = ".pdf"
                elif "zip" in ct or "x-zip" in ct:
                    ext = ".zip"
                elif "word" in ct:
                    ext = ".docx"
                elif "excel" in ct or "sheet" in ct:
                    ext = ".xlsx"
                elif "text/plain" in ct:
                    ext = ".txt"
                elif "json" in ct:
                    ext = ".json"
                elif "audio" in ct:
                    ext = ".mp3"
                elif "video" in ct:
                    ext = ".mp4"
            else:
                ext = ".bin"

            if self.file_storage_dir:
                save_dir = os.path.join(self.file_storage_dir, time.strftime("%Y-%m"))
            else:
                save_dir = os.path.join(self.attach or ".", "wechat_files")
            os.makedirs(save_dir, exist_ok=True)

            base = suggested_name or f"wechat_{int(time.time()*1000)}"
            if not os.path.splitext(base)[1] and ext:
                base += ext
            fname = self._uniq_name(save_dir, base)
            path = os.path.join(save_dir, fname)
            with open(path, "wb") as f:
                f.write(r.content)
            return path
        except Exception as exc:
            log.error(f"[{self.name}] 下载文件异常: {exc}")
            return None
