

import os
import time
import threading

from core.extension import Extension, Context
from core.loggingx import build_logger
from .watcher import Watcher
from .matcher import match_contact
from .forwarder import push_file

log = build_logger("miloto.ext.file_relay")

class FileRelay(Extension):
    name = "file_relay"

    def on_load(self, ctx: Context) -> None:
        self.ctx = ctx
        self.recent = []
        self.lock = threading.Lock()

        download_dir = ctx.settings.download_dir
        whitelist = ctx.settings.file_whitelist
        self.whitelist = set(whitelist)
        if download_dir:
            self.watcher = Watcher(download_dir, ctx.bus, log)
            self.watcher.start()
            log.info(f"文件转发已启用，监听目录: {download_dir}")
        else:
            log.warning("未配置文件存放目录(files.download_dir)，文件转发未启用")

    def on_inbound(self, msg):

        contact = msg.get("sourceName", "") or msg.get("talkerName", "") or "未知"
        with self.lock:
            self.recent.append((time.time(), contact))
            if len(self.recent) > 50:
                self.recent.pop(0)
        return msg

    def on_file(self, payload) -> None:
        path, meta = payload
        if self.whitelist:
            base = os.path.basename(path)
            if not any(w in base for w in self.whitelist):
                return
        mtime = meta.get("mtime", time.time())
        with self.lock:
            recent = list(self.recent)
        contact = match_contact(recent, mtime)
        if not contact:
            log.warning(f"无法归属文件，跳过: {os.path.basename(path)}")
            return
        push_file(self.ctx.pusher, self.ctx.settings, contact, path)
