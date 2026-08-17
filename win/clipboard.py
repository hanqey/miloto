

import ctypes
import ctypes.wintypes
import threading
import time

from core.loggingx import build_logger

log = build_logger("miloto.clip")

_CLIP_LOCK = threading.Lock()

_USER32 = ctypes.windll.user32
_KERNEL32 = ctypes.windll.kernel32

VK_CONTROL = 0x11
VK_V = 0x56
KEYEVENTF_KEYUP = 0x0002

try:
    _USER32.OpenClipboard.argtypes = [ctypes.wintypes.HWND]
    _USER32.OpenClipboard.restype = ctypes.wintypes.BOOL
    _USER32.CloseClipboard.argtypes = []
    _USER32.CloseClipboard.restype = ctypes.wintypes.BOOL
    _USER32.EmptyClipboard.argtypes = []
    _USER32.EmptyClipboard.restype = ctypes.wintypes.BOOL
    _USER32.SetClipboardData.argtypes = [ctypes.wintypes.UINT, ctypes.wintypes.HANDLE]
    _USER32.SetClipboardData.restype = ctypes.wintypes.HANDLE
    _KERNEL32.GlobalAlloc.argtypes = [ctypes.wintypes.UINT, ctypes.c_size_t]
    _KERNEL32.GlobalAlloc.restype = ctypes.wintypes.HANDLE
    _KERNEL32.GlobalLock.argtypes = [ctypes.wintypes.HANDLE]
    _KERNEL32.GlobalLock.restype = ctypes.wintypes.LPVOID
    _KERNEL32.GlobalUnlock.argtypes = [ctypes.wintypes.HANDLE]
    _KERNEL32.GlobalUnlock.restype = ctypes.wintypes.BOOL
except Exception:
    pass

def _last_error() -> str:

    code = 0
    try:
        import win32api
        code = win32api.GetLastError()
    except Exception:
        try:
            code = ctypes.GetLastError()
        except Exception:
            pass
    if code:
        try:
            import win32api
            return "%d (%s)" % (code, win32api.FormatMessage(code).strip())
        except Exception:
            return str(code)
    return "0(无)"

def set_clipboard_text(text: str) -> bool:

    text = text or ""
    last_err = None

    delays = [0.03, 0.06, 0.1, 0.15, 0.25, 0.4, 0.6, 1.0]
    with _CLIP_LOCK:
        for i, d in enumerate(delays):
            try:
                if _set_pywin32(text):
                    return True
            except Exception as exc:
                last_err = exc
            try:
                if _set_ctypes(text):
                    return True
            except Exception as exc:
                last_err = exc
            if i < len(delays) - 1:
                time.sleep(d)
    log.error("[剪贴板] 写入最终失败：last_err=%s, GetLastError=%s", last_err, _last_error())
    return False

def _set_pywin32(text: str) -> bool:
    try:
        import win32clipboard
        import win32con
    except Exception:
        return False
    try:

        try:
            win32clipboard.CloseClipboard()
        except Exception:
            pass
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
            return True
        finally:
            win32clipboard.CloseClipboard()
    except Exception as exc:

        log.debug("[剪贴板][pywin32] OpenClipboard/Set 失败：%s, GetLastError=%s",
                  exc, _last_error())
        try:
            win32clipboard.CloseClipboard()
        except Exception:
            pass
        return False

def _set_ctypes(text: str) -> bool:
    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0042
    try:

        try:
            _USER32.CloseClipboard()
        except Exception:
            pass
        if not _USER32.OpenClipboard(0):
            log.debug("[剪贴板][ctypes] OpenClipboard 返回 False, GetLastError=%s", _last_error())
            return False
        try:
            _USER32.EmptyClipboard()
            size = (len(text) + 1) * 2
            h = _KERNEL32.GlobalAlloc(GMEM_MOVEABLE, size)
            if not h:
                return False
            p = _KERNEL32.GlobalLock(h)
            if not p:
                return False
            try:
                buf = ctypes.create_unicode_buffer(text + "\0")
                ctypes.memmove(p, ctypes.addressof(buf), size)
            finally:
                _KERNEL32.GlobalUnlock(h)
            _USER32.SetClipboardData(CF_UNICODETEXT, h)
            return True
        finally:
            _USER32.CloseClipboard()
    except Exception as exc:
        log.debug("[剪贴板][ctypes] 写入异常：%s, GetLastError=%s", exc, _last_error())
        try:
            _USER32.CloseClipboard()
        except Exception:
            pass
        return False

def paste_text(text: str) -> bool:

    if not set_clipboard_text(text):
        return False
    return _send_ctrl_v()

def _send_ctrl_v() -> bool:
    try:
        _USER32.keybd_event(VK_CONTROL, 0, 0, 0)
        time.sleep(0.02)
        _USER32.keybd_event(VK_V, 0, 0, 0)
        time.sleep(0.02)
        _USER32.keybd_event(VK_V, 0, KEYEVENTF_KEYUP, 0)
        time.sleep(0.02)
        _USER32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
        return True
    except Exception:
        return False
    finally:

        for vk in (VK_CONTROL, 0x12, 0x10, 0x5B, 0x5C):
            try:
                _USER32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
            except Exception:
                pass
