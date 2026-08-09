

import ctypes
import time

import uiautomation as auto

from core.loggingx import build_logger

log = build_logger("miloto.win")

EXCLUDE = {"WorkerW", "Progman", "Shell_TrayWnd", "CabinetWClass"}

def find_hwnd() -> int:

    user32 = ctypes.windll.user32

    hwnd = user32.FindWindowW(None, "微信")
    if hwnd:
        return hwnd

    hwnd = user32.FindWindowW("Qt51514QWindowIcon", None)
    if hwnd:
        return hwnd

    candidates = []

    def _cb(h, _):
        buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(h, buf, 256)
        title = buf.value
        rect = ctypes.wintypes.RECT()
        user32.GetWindowRect(h, ctypes.byref(rect))
        w = rect.right - rect.left
        hgt = rect.bottom - rect.top
        if w < 80 or hgt < 80:
            return True
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(h, cls, 256)
        clsname = cls.value
        if clsname in EXCLUDE:
            return True

        title_hit = ("微信" in title) or ("WeChat" in title)
        cls_hit = clsname == "Qt51514QWindowIcon"
        if not (title_hit or cls_hit):
            return True

        score = (w * hgt) + (1 << 30 if cls_hit else 0)
        candidates.append((h, score))
        return True

    EnumWindows = user32.EnumWindows
    WNDENUMPROC = ctypes.WINFUNCTYPE(
        ctypes.wintypes.BOOL, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM
    )
    EnumWindows.argtypes = [WNDENUMPROC, ctypes.wintypes.LPARAM]
    EnumWindows.restype = ctypes.wintypes.BOOL
    EnumWindows(WNDENUMPROC(_cb), 0)
    if not candidates:
        return 0
    candidates.sort(key=lambda x: x[1], reverse=True)
    return candidates[0][0]

def restore_window(hwnd: int) -> bool:

    user32 = ctypes.windll.user32
    SW_RESTORE, SW_SHOW = 9, 5
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
        time.sleep(0.3)
        return True
    if not user32.IsWindowVisible(hwnd):
        user32.ShowWindow(hwnd, SW_SHOW)
        time.sleep(0.2)
        return True
    return False

def reacquire(hwnd: int):

    factory = getattr(auto, "ControlFromHandle", None)
    if factory is None:
        return None
    try:
        ctrl = factory(hwnd)
    except Exception:
        return None
    if ctrl is None or not ctrl.Exists():
        return None
    try:
        r = ctrl.BoundingRectangle
        if r.width() < 50 or r.height() < 50:
            return None
    except Exception:
        return None
    return ctrl

def locate() -> object:

    user32 = ctypes.windll.user32
    hwnd = find_hwnd()
    if hwnd:
        ctrl = reacquire(hwnd)
        if ctrl is not None:
            return ctrl

    try:
        for w in auto.GetRootControl().GetChildren():
            try:
                name = w.Name
            except Exception:
                name = ""
            if "微信" in name or "WeChat" in name:
                return w
    except Exception:
        pass
    return None

def activate(control) -> None:

    hwnd = None
    try:
        hwnd = control.NativeWindowHandle
    except Exception:
        pass
    if not hwnd:
        return
    user32 = ctypes.windll.user32
    try:

        user32.AllowSetForegroundWindow(0xFFFFFFFF)
    except Exception:
        pass
    try:
        fg = user32.GetForegroundWindow()
        fg_tid = user32.GetWindowThreadProcessId(fg, None)
        we_tid = user32.GetWindowThreadProcessId(hwnd, None)
        attached = False
        if fg_tid and we_tid and fg_tid != we_tid:
            try:
                user32.AttachThreadInput(fg_tid, we_tid, True)
                attached = True
            except Exception:
                pass
        try:
            user32.ShowWindow(hwnd, 9)
            user32.SetForegroundWindow(hwnd)
            user32.BringWindowToTop(hwnd)
            user32.SetFocus(hwnd)
        finally:
            if attached:
                try:
                    user32.AttachThreadInput(fg_tid, we_tid, False)
                except Exception:
                    pass
    except Exception:
        pass
