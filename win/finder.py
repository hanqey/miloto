

import ctypes
import time

import uiautomation as auto

from core.loggingx import build_logger

log = build_logger("miloto.win")

class _HwndProxy:

    uia_unavailable = True

    def __init__(self, hwnd: int):
        self._hwnd = hwnd

    @property
    def NativeWindowHandle(self) -> int:
        return self._hwnd

    def Exists(self) -> bool:
        return True

    def SetFocus(self) -> None:

        try:
            activate(self)
        except Exception:
            pass

    def SendKeys(self, *args, **kwargs):

        raise RuntimeError("UIA 不可用：键盘方案应通过剪贴板 Ctrl+V 输入，不走 SendKeys")

EXCLUDE = {"WorkerW", "Progman", "Shell_TrayWnd", "CabinetWClass"}

def find_hwnd() -> int:

    user32 = ctypes.windll.user32

    user32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
    user32.FindWindowW.restype = ctypes.c_void_p

    hwnd = user32.FindWindowW(None, "微信")
    if hwnd:
        return int(hwnd)

    hwnd = user32.FindWindowW("Qt51514QWindowIcon", None)
    if hwnd:
        return int(hwnd)

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

        cls_hit = (
            clsname == "Qt51514QWindowIcon"
            or (clsname.startswith("Qt") and clsname.endswith("QWindowIcon"))
            or clsname == "WeChatMainWndForPC"
            or "QMainWindow" in clsname
        )
        if not (title_hit or cls_hit):
            return True

        score = (w * hgt) + (1 << 30 if cls_hit else 0) + (1 << 29 if title_hit else 0)
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

def _wake_via_tray_icon(hwnd: int) -> bool:

    try:
        import uiautomation as auto
    except Exception:
        return False
    try:
        user32 = ctypes.windll.user32

        def _search(name, cls="SystemTray.NormalButton", max_depth=18):

            try:
                root = auto.GetRootControl()
            except Exception:
                return None
            stack = [(root, 0)]
            while stack:
                ctrl, depth = stack.pop()
                if depth > max_depth:
                    continue
                try:
                    if (ctrl.Name or "") == name and (ctrl.ClassName or "") == cls:
                        return ctrl
                except Exception:
                    pass
                try:
                    for c in ctrl.GetChildren():
                        stack.append((c, depth + 1))
                except Exception:
                    pass
            return None

        def _click(ctrl):
            if ctrl is None:
                return False
            try:
                if not ctrl.Exists(0):
                    return False
            except Exception:
                pass
            for action in ("click", "invoke"):
                try:
                    if action == "click":
                        ctrl.Click()
                    else:
                        if not hasattr(ctrl, "Invoke"):
                            continue
                        ctrl.Invoke()
                    return True
                except Exception:
                    continue

            try:
                r = ctrl.BoundingRectangle
                if r.width() > 0 and r.height() > 0:
                    cx, cy = r.x + r.width() // 2, r.y + r.height() // 2
                    user32.SetCursorPos(cx, cy)
                    user32.mouse_event(0x0002, 0, 0, 0, 0)
                    user32.mouse_event(0x0004, 0, 0, 0, 0)
                    return True
            except Exception:
                pass
            return False

        icon = _search("微信", "SystemTray.NormalButton")
        if icon is None:

            expand = _search("显示隐藏的图标", "SystemTray.NormalButton")
            if expand is not None:
                _click(expand)

                for _ in range(20):
                    time.sleep(0.1)
                    icon = _search("微信", "SystemTray.NormalButton")
                    if icon is not None:
                        break
                if icon is None:
                    icon = _search("微信", "SystemTray.NormalButton")
        if icon is None:
            return False
        _click(icon)

        for _ in range(10):
            time.sleep(0.15)
            if user32.IsWindowVisible(hwnd):
                return True
        return user32.IsWindowVisible(hwnd)
    except Exception:
        return False

def restore_window(hwnd: int) -> bool:

    user32 = ctypes.windll.user32
    SW_RESTORE = 9
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
        time.sleep(0.3)
        return True
    if not user32.IsWindowVisible(hwnd):

        if _wake_via_tray_icon(hwnd):
            return True
        SW_SHOW, SW_MINIMIZE, SW_RESTORE = 5, 6, 9
        user32.ShowWindow(hwnd, SW_SHOW)
        time.sleep(0.05)
        user32.ShowWindow(hwnd, SW_MINIMIZE)
        time.sleep(0.08)
        user32.ShowWindow(hwnd, SW_RESTORE)
        time.sleep(0.3)
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

        return _HwndProxy(hwnd)

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

        user32.ShowWindow(hwnd, 9)
        for _ in range(5):
            user32.SetForegroundWindow(hwnd)
            user32.BringWindowToTop(hwnd)
            time.sleep(0.05)
            try:
                fg = user32.GetForegroundWindow()
            except Exception:
                fg = None
            if fg == hwnd:
                break

        time.sleep(0.12)
    except Exception:
        pass
