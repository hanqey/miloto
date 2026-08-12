

import os
import random
import time
import threading
import queue
import ctypes
from ctypes import wintypes

import uiautomation as auto

from core.loggingx import build_logger
from core.runtime import paused
from win import finder

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
VK_CONTROL = 0x11
VK_F = 0x46
VK_A = 0x41
VK_V = 0x56
VK_DELETE = 0x2E
VK_RETURN = 0x0D

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.c_void_p),
    ]

class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT)]

class INPUT(ctypes.Structure):
    _anonymous_ = ("_union",)
    _fields_ = [("type", ctypes.c_ulong), ("_union", _INPUTUNION)]

log = build_logger("miloto.typist")

_MSG_GAP = (0.15, 0.5)
_COOLDOWN = 0.3
_MAX_PER_MIN = 20
_SESSION_TTL = 10

class WeChatSender:
    def __init__(self, settings=None):

        self._settings = settings
        self._window = None
        self._ready = False
        self._lock = threading.Lock()
        self._last_send = 0.0
        self._minute_count = 0
        self._minute_ts = time.time()
        self.last_contact = None
        self.last_send_ts = 0.0

        self._outbox = queue.Queue()
        self._worker = None
        self._worker_started = False

    def send_text(self, contact: str, text: str) -> bool:
        if not self._ensure(contact):
            return False

        if not self._ensure_foreground():
            log.warning("[键盘] 发送前微信未置前，正文可能未进入输入框")

        if not self._type(text):
            return False
        log.debug(f"[键盘] 已粘贴正文({len(text)}字)，准备回车发送（屏幕输入框应显示该内容）")
        self._throttle()

        try:
            self._send_input(VK_RETURN)
            time.sleep(0.2)
        except Exception as exc:
            log.error(f"发送回车失败: {exc}")
            return False
        self.last_contact = contact
        self.last_send_ts = time.time()
        return True

    def send_image(self, contact: str, path: str) -> bool:
        if not self._ensure(contact):
            return False
        ok = self._paste_file(contact, path, is_video=False)
        if ok:
            self.last_contact = contact
            self.last_send_ts = time.time()
            self._clean_temp(path)
        return ok

    def send_voice(self, contact: str, path: str) -> bool:
        if not self._ensure(contact):
            return False
        ok = self._paste_file(contact, path, is_video=False)
        if ok:
            self.last_contact = contact
            self.last_send_ts = time.time()
            self._clean_temp(path)
        return ok

    def open_chat(self, contact: str) -> bool:

        with self._lock:
            return self._ensure(contact)

    def enqueue(self, kind: str, contact: str, payload) -> None:

        if not self._worker_started:
            with self._lock:
                if not self._worker_started:
                    self._start_worker()
        self._outbox.put((kind, contact, payload))

    def _start_worker(self) -> None:
        self._worker = threading.Thread(
            target=self._run_worker, name="wechat-sender", daemon=True
        )
        self._worker_started = True
        self._worker.start()

    def _run_worker(self) -> None:

        while True:
            if paused.is_set():
                time.sleep(0.3)
                continue
            try:
                kind, contact, payload = self._outbox.get(timeout=0.5)
            except queue.Empty:
                continue

            if paused.is_set():
                self._outbox.put((kind, contact, payload))
                time.sleep(0.3)
                continue
            try:
                with self._lock:
                    if kind == "text":
                        self.send_text(contact, payload)
                    elif kind == "image":
                        self.send_image(contact, payload)
                    elif kind == "voice":
                        self.send_voice(contact, payload)
                    else:
                        log.warning(f"发送队列遇到未知类型: {kind}")
            except Exception as exc:
                log.error(f"发送队列处理失败: {exc}")
            finally:
                self._outbox.task_done()

    def _clean_temp(self, path: str) -> None:

        if path and "tmp" in path:
            try:
                os.unlink(path)
            except Exception:
                pass

    def _throttle(self) -> None:

        now = time.time()
        if now - self._minute_ts > 60:
            self._minute_ts = now
            self._minute_count = 0
        self._minute_count += 1
        if self._minute_count > _MAX_PER_MIN:
            time.sleep(max(0, 60 - (now - self._minute_ts)))
        gap = _COOLDOWN + random.uniform(*_MSG_GAP)
        time.sleep(gap)

    def _ensure(self, contact: str) -> bool:
        from core import runtime
        if runtime.paused.is_set():
            return False
        if not self._ready or self._window is None:
            self._window = finder.locate()
            self._ready = self._window is not None
        if not self._ready:
            return False

        cache_on = bool(self._settings.get("sender.session_cache", False)) if self._settings else False
        if (cache_on and self.last_contact == contact
                and (time.time() - self.last_send_ts) < _SESSION_TTL):
            if self._ensure_foreground():
                return True
            log.warning("[会话缓存] 微信未置前，降级为完整切会话")
        return self._switch_contact(contact)

    def _switch_contact(self, contact: str) -> bool:

        try:
            self._activate_window()
            box, diag = self._find_search_box()
            if box is not None:
                self._set_value(box, contact)
                time.sleep(0.4)

                self._window.SendKeys("{Enter}")
                time.sleep(0.5)
                return True

            if diag:
                names = "、".join(f"{d[1]!r}@d{d[0]}" for d in diag[:12])
                log.warning(f"UIA 找不到搜索框（窗口内 Edit: {names}），改用键盘方案")
            else:
                log.warning("UIA 找不到搜索框（窗口内无任何 Edit 控件，微信 4.x 常见），改用键盘方案")
            return self._switch_contact_keyboard(contact)
        except Exception as exc:
            log.error(f"切换联系人失败: {exc}")
            return False

    def _switch_contact_keyboard(self, contact: str) -> bool:

        try:
            log.debug(f"[键盘] 切换联系人开始: {contact}")
            if not self._ensure_foreground():
                log.warning("[键盘] 微信窗口未能置前，键可能发丢（手动把微信点出来再试）")
            self._send_combo(VK_CONTROL, VK_F)
            time.sleep(0.6)
            log.debug("[键盘] 已发 Ctrl+F（屏幕应出现搜索框聚焦）")
            self._send_combo(VK_CONTROL, VK_A)
            time.sleep(0.1)
            self._send_input(VK_DELETE)
            time.sleep(0.2)
            if not self._paste_text(contact):
                return False
            log.debug(f"[键盘] 已粘贴昵称到搜索框: {contact}（屏幕搜索框应显示该昵称）")
            time.sleep(0.4)
            self._send_input(VK_RETURN)
            time.sleep(0.6)
            log.debug(f"[键盘] 已发回车（应打开与 {contact} 的会话，焦点回到输入框）")
            return True
        except Exception as exc:
            log.error(f"键盘切换联系人失败: {exc}")
            return False

    def _send_input(self, *vks) -> None:

        events = [(vk, True) for vk in vks] + [(vk, False) for vk in vks]
        self._send_events(*events)

    def _send_combo(self, modifier: int, vk: int) -> None:

        self._send_events((modifier, True), (vk, True), (vk, False), (modifier, False))

    def _send_events(self, *events) -> None:

        user32 = ctypes.windll.user32
        for vk, down in events:
            flags = 0 if down else KEYEVENTF_KEYUP
            user32.keybd_event(vk, 0, flags, 0)
            time.sleep(0.01)

    def _ensure_foreground(self) -> bool:

        hwnd = finder.find_hwnd()
        if not hwnd:
            return False
        user32 = ctypes.windll.user32
        for _ in range(3):
            try:
                if self._window is not None:
                    finder.activate(self._window)
                else:
                    user32.SetForegroundWindow(hwnd)
            except Exception:
                pass
            time.sleep(0.2)
            try:
                fg = user32.GetForegroundWindow()
            except Exception:
                fg = None
            if fg == hwnd:
                return True
        return False

    def _clip_set_text(self, text: str) -> bool:

        try:
            import win32clipboard, win32con
            win32clipboard.OpenClipboard()
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
            win32clipboard.CloseClipboard()
            return True
        except Exception:
            pass
        try:
            CF_UNICODETEXT = 13
            GMEM_MOVEABLE = 0x0042
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            if not user32.OpenClipboard(0):
                return False
            try:
                user32.EmptyClipboard()
                h = kernel32.GlobalAlloc(GMEM_MOVEABLE, (len(text) + 1) * 2)
                if not h:
                    return False
                p = kernel32.GlobalLock(h)
                if not p:
                    return False
                try:
                    buf = ctypes.create_unicode_buffer(text + "\0")
                    ctypes.memmove(p, ctypes.addressof(buf), (len(text) + 1) * 2)
                finally:
                    kernel32.GlobalUnlock(h)
                user32.SetClipboardData(CF_UNICODETEXT, h)
                return True
            finally:
                user32.CloseClipboard()
        except Exception:
            return False

    def _paste_text(self, text: str) -> bool:

        if self._clip_set_text(text):
            try:
                self._send_combo(VK_CONTROL, VK_V)
                time.sleep(0.3)
                return True
            except Exception as exc:
                log.error(f"粘贴失败: {exc}")
                return False
        try:
            self._window.SendKeys(text)
            time.sleep(0.3)
            return True
        except Exception as exc:
            log.error(f"粘贴/键入失败: {exc}")
            return False

    def _find_search_box(self):

        try:
            box = self._window.EditControl()
            if box is not None and box.Exists():
                return box, []
        except Exception:
            pass

        try:
            box = self._window.Control(searchDepth=6, ClassName="Edit")
            if box is not None and box.Exists():
                return box, []
        except Exception:
            pass

        diag = []
        found = self._walk_edits(self._window, "搜索", 0, 8, diag)
        if found is not None:
            return found, diag

        return None, diag

    def _walk_edits(self, node, keyword, depth, max_depth, diag):

        try:
            is_edit = node.ControlType == auto.ControlType.Edit
        except Exception:
            is_edit = False
        try:
            name = node.Name or ""
        except Exception:
            name = ""
        if is_edit:
            diag.append((depth, name))
            if keyword and keyword in name:
                return node
        if depth >= max_depth:
            return None
        try:
            for child in node.GetChildren():
                res = self._walk_edits(child, keyword, depth + 1, max_depth, diag)
                if res is not None:
                    return res
        except Exception:
            pass
        return None

    def _activate_window(self) -> None:

        hwnd = finder.find_hwnd()
        if not hwnd:
            return
        was_hidden = finder.restore_window(hwnd)
        if was_hidden or self._window is None:
            ctrl = finder.reacquire(hwnd)
            if ctrl is not None:
                self._window = ctrl
                self._ready = True
            else:
                self._window = finder.locate()
                self._ready = self._window is not None
        if self._window is not None:
            finder.activate(self._window)

    def _set_value(self, ctrl, text: str) -> None:

        vp = ctrl.GetValuePattern()
        if vp is not None:
            vp.SetValue(text)
        else:

            try:
                ctrl.SetValue(text)
            except Exception:

                self._window.SetFocus()
                ctrl.SendKeys(text)

    def _type(self, text: str) -> bool:

        try:
            self._window.SetFocus()
            return self._paste_text(text)
        except Exception as exc:
            log.error(f"键入失败: {exc}")
            return False

    def _paste_file(self, contact: str, path: str, is_video: bool) -> bool:

        if not os.path.exists(path):
            log.warning(f"文件不存在: {path}")
            return False
        try:
            import win32clipboard
            import win32con
            with open(path, "rb") as f:
                data = f.read()
            win32clipboard.OpenClipboard()
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32con.CF_DIB, data)
            win32clipboard.CloseClipboard()
            self._send_combo(VK_CONTROL, VK_V)
            time.sleep(0.6)
            self._send_input(VK_RETURN)
            return True
        except Exception as exc:
            log.error(f"粘贴文件失败: {exc}")
            return False
