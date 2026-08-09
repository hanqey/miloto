

import os
import time
import ctypes

import win32gui
import win32con
import win32api

from core.loggingx import build_logger
from win import finder
from .vision_locate import locate_button

log = build_logger("miloto.ext.origimg")

_MIN_BYTES = 20000
_WAIT_SEC = 30

class Grabber:
    def __init__(self, ctx, ext):
        self.ctx = ctx
        self.ext = ext
        self._set_dpi_aware()

    def _set_dpi_aware(self) -> None:

        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass

    def capture_original(self, contact: str, talker: str) -> str | None:

        hwnd = finder.find_hwnd()
        if not hwnd:
            log.warning("找不到微信窗口")
            return None
        if not self.ctx.sender.open_chat(contact):
            log.warning("无法打开会话，放弃原图抓取")
            return None
        time.sleep(0.6)

        shot = self._screenshot(hwnd)
        if shot:
            bcoord = locate_button(shot, self.ext.vision_url, self.ext.vision_key,
                                   self.ext.vision_model, "最近一条图片消息的缩略图")
            if bcoord:
                self._click(bcoord)
                time.sleep(1.2)

        shot2 = self._screenshot(hwnd)
        if shot2:
            vcoord = locate_button(shot2, self.ext.vision_url, self.ext.vision_key,
                                   self.ext.vision_model, "查看原图 按钮")
            if vcoord:
                self._click(vcoord)
            else:
                log.warning("未定位到查看原图按钮，尝试经验位置")
                self._click_default_view_original(hwnd)
        else:
            self._click_default_view_original(hwnd)

        return self._wait_original()

    def _screenshot(self, hwnd: int) -> str | None:

        try:
            from PIL import ImageGrab
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            img = ImageGrab.grab(bbox=(left, top, right, bottom))
            base = self.ctx.settings.attachments or "."
            os.makedirs(base, exist_ok=True)
            path = os.path.join(base, "origimg_shot.png")
            img.save(path)
            return path
        except Exception as exc:
            log.error(f"截图失败: {exc}")
            return None

    def _click(self, coords) -> None:

        try:
            x, y = int(coords[0]), int(coords[1])
            win32api.SetCursorPos((x, y))
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, x, y, 0, 0)
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, x, y, 0, 0)
            time.sleep(0.3)
        except Exception as exc:
            log.error(f"点击失败: {exc}")

    def _click_default_view_original(self, hwnd: int) -> None:

        try:
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            w = right - left
            h = bottom - top
            self._click((left + int(w * 0.62), top + int(h * 0.78)))
        except Exception:
            pass

    def _wait_original(self) -> str | None:

        d = self.ext.image_dir
        if not d or not os.path.isdir(d):
            log.warning("未配置/不存在微信图片目录")
            return None
        deadline = time.time() + _WAIT_SEC
        best = None
        while time.time() < deadline:
            now = time.time()
            cand = None
            cand_sz = 0
            for root, _, files in os.walk(d):
                for fn in files:
                    if not fn.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp")):
                        continue
                    p = os.path.join(root, fn)
                    try:
                        sz = os.path.getsize(p)
                        mt = os.path.getmtime(p)
                    except Exception:
                        continue
                    age = now - mt
                    if age < 2 or age > _WAIT_SEC + 5:
                        continue
                    if sz < _MIN_BYTES:
                        continue
                    if sz > cand_sz:
                        cand_sz = sz
                        cand = p
            if cand:

                time.sleep(1.0)
                try:
                    if os.path.getsize(cand) >= cand_sz:
                        return cand
                except Exception:
                    pass
            time.sleep(1.0)
        return best
