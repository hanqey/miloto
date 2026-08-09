

import re
import time
import threading

from core.extension import Extension, Context
from core.loggingx import build_logger
from .timer import parse_when

log = build_logger("miloto.ext.scheduler")

class Scheduler(Extension):
    name = "scheduler"

    def on_load(self, ctx: Context) -> None:
        self.ctx = ctx
        self.jobs = []
        for job in (ctx.settings.get("scheduler", []) or []):
            when = parse_when(job.get("when", "every 60"))
            self.jobs.append({
                "when": when,
                "contact": job.get("contact", ""),
                "text": job.get("text", ""),
                "last": time.time(),
            })
        self.commands = ctx.settings.get("commands", []) or []

        if self.jobs:
            log.info(f"定时任务已加载 {len(self.jobs)} 条")
        else:
            log.info("未配置定时任务")
        if self.commands:
            log.info(f"远程命令已加载 {len(self.commands)} 条")

    def on_tick(self, ts=None) -> None:
        now = time.time()
        for job in self.jobs:
            when = job["when"]
            if isinstance(when, tuple) and when[0] == "daily":
                h, m = when[1], when[2]
                local = time.localtime(now)
                if (local.tm_hour, local.tm_min) == (h, m) and now - job["last"] > 60:
                    job["last"] = now
                    self._fire(job)
            else:
                if now - job["last"] >= when:
                    job["last"] = now
                    self._fire(job)

    def _fire(self, job) -> None:
        contact, text = job["contact"], job["text"]
        if not contact or not text:
            return
        threading.Thread(target=self.ctx.sender.send_text,
                         args=(contact, text), daemon=True).start()

    def on_outbound(self, req) -> bool | None:

        text = (req.get("text") or "").strip()
        contact = req.get("contact", "")
        for cmd in self.commands:
            pattern = cmd.get("match", "")
            if not pattern:
                continue
            try:
                hit = re.search(pattern, text) is not None
            except Exception:
                hit = False
            if not hit:
                continue
            action = cmd.get("action", "")
            arg = cmd.get("arg", "")
            if action == "original":
                orig = self.ctx.extensions.get("origimg")
                if orig:
                    threading.Thread(target=orig.request_original,
                                     args=(contact,), daemon=True).start()
                else:
                    log.warning("收到原图命令，但 origimg 扩展未启用")
            elif action == "echo":
                if arg:
                    threading.Thread(target=self.ctx.sender.send_text,
                                     args=(contact, arg), daemon=True).start()
            else:
                log.info(f"未知命令动作: {action}")
            return True
        return None
