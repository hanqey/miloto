

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

def _get_exe(hwnd: int) -> str:

    try:
        pid = ctypes.wintypes.DWORD()
        user32 = ctypes.windll.user32
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return ""
        PROCESS_QUERY_INFORMATION = 0x0400
        PROCESS_VM_READ = 0x0010
        kernel32 = ctypes.windll.kernel32
        ph = kernel32.OpenProcess(
            PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid.value
        )
        if not ph:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(1024)
            psapi = ctypes.windll.psapi
            psapi.GetModuleFileNameExW.argtypes = [
                ctypes.wintypes.HANDLE,
                ctypes.wintypes.HMODULE,
                ctypes.c_wchar_p,
                ctypes.wintypes.DWORD,
            ]
            psapi.GetModuleFileNameExW.restype = ctypes.wintypes.DWORD
            n = psapi.GetModuleFileNameExW(ph, 0, buf, 1024)
            return buf.value.lower() if n else ""
        finally:
            kernel32.CloseHandle(ph)
    except Exception:
        return ""

def find_hwnd() -> int:

    user32 = ctypes.windll.user32

    user32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
    user32.FindWindowW.restype = ctypes.c_void_p

    hwnd = user32.FindWindowW(None, "微信")
    if hwnd:
        return int(hwnd)

    candidates = []

    def _cb(h, _):

        if not user32.IsWindowVisible(h):
            return True
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

        exe = _get_exe(h)
        exe_hit = exe.endswith("wechat.exe") or exe.endswith("weixin.exe")

        title_hit = ("微信" in title) or ("WeChat" in title) or ("Weixin" in title)

        cls_hit = (
            clsname == "Qt51514QWindowIcon"
            or (clsname.startswith("Qt") and clsname.endswith("QWindowIcon"))
            or clsname == "WeChatMainWndForPC"
            or "QMainWindow" in clsname
        )
        if not (title_hit or cls_hit or exe_hit):
            return True

        score = (w * hgt) + (1 << 31 if exe_hit else 0) + (1 << 30 if cls_hit else 0) + (1 << 29 if title_hit else 0)
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

def _is_wechat_name(name):

    compact = " ".join(str(name or "").split()).strip()
    if compact == "微信":
        return True
    if compact.startswith("微信"):
        suffix = compact[2:3]
        return not suffix or suffix in " -–—:：([（【\r\n"
    folded = compact.casefold()
    for prefix in ("wechat", "weixin"):
        if folded == prefix:
            return True
        after = folded[len(prefix):len(prefix) + 1]
        if folded.startswith(prefix) and after in {" ", "-", "–", "—", ":", "("}:
            return True
    return False

def _is_overflow_button(name):
    compact = " ".join(str(name or "").split()).strip().casefold()
    return (
        "显示隐藏的图标" in compact
        or "显示隐藏图标" in compact
        or "show hidden icons" in compact
        or "show hidden icon" in compact
    )

def _wake_via_tray_icon(hwnd: int) -> bool:

    try:
        from win.vrec.tray import comtypes_scan_tray
    except Exception as e:
        log.warning("[托盘唤醒] 无法加载 win.vrec.tray（comtypes 扫描）：%s", e)
        return False
    try:
        user32 = ctypes.windll.user32
        t0 = time.perf_counter()

        def _scan_area(area):

            try:
                return [r for (_n, r) in comtypes_scan_tray(area) if _is_wechat_name(_n)]
            except Exception:
                return []

        def _scan_area_for(area, predicate):
            try:
                for (_n, r) in comtypes_scan_tray(area):
                    if predicate(_n):
                        return r
            except Exception:
                pass
            return None

        def _click(rect):
            if not rect:
                return False
            left, top, right, bottom = rect
            if right - left < 3 or bottom - top < 3:
                return False
            import random
            from win import human_input

            w = right - left
            h = bottom - top
            fx = random.uniform(0.30, 0.60)
            fy = random.uniform(0.30, 0.60)
            cx = left + max(1, int(w * fx))
            cy = top + max(1, int(h * fy))
            try:

                ok = human_input.human_click(cx, cy, before=0.08, hold=0.07, after=0.09)
                if ok:
                    log.debug("[托盘唤醒] 拟人点击图标坐标 (%d,%d)", cx, cy)
                return ok
            except Exception as e:
                log.debug("[托盘唤醒] 真实鼠标点击失败: %s", e)
                return False

        log.debug("[托盘唤醒] 开始：hwnd=%s，comtypes 原生 UIA 扫描通知区…", hwnd)
        t_scan = time.perf_counter()
        mains = _scan_area("main")
        log.debug("[托盘唤醒] 主任务栏扫描完成，命中微信候选 %d 个（耗时 %.2fs）",
                 len(mains), time.perf_counter() - t_scan)

        icon = None
        if mains:
            icon = mains[0]
            if len(mains) > 1:
                log.warning("[托盘唤醒] 主任务栏检测到 %d 个微信候选，点击第一个", len(mains))
            log.info("[托盘唤醒] 点击主任务栏微信图标以唤醒")
            _click(icon)
        else:

            log.info("[托盘唤醒] 主任务栏无微信，尝试展开溢出区")
            expand = _scan_area_for("main", _is_overflow_button)
            if expand is not None:
                log.info("[托盘唤醒] 找到「显示隐藏的图标」按钮，点击展开")
                _click(expand)

                for i in range(20):
                    time.sleep(0.12)
                    t_p = time.perf_counter()
                    cand = _scan_area("main") + _scan_area("overflow")
                    if cand:
                        icon = cand[0]
                        log.info("[托盘唤醒] 展开后第 %d 轮找到微信图标（耗时 %.2fs）",
                                 i + 1, time.perf_counter() - t_p)
                        break
            else:
                log.warning("[托盘唤醒] 未找到「显示隐藏的图标」按钮，无法展开溢出区")

        if icon is None:
            log.warning("[托盘唤醒] 始终未找到微信托盘图标，UIA 路径失败（将退回热键）")
            return False
        if not mains:
            log.info("[托盘唤醒] 点击溢出区微信图标以唤醒")
            _click(icon)

        for i in range(10):
            time.sleep(0.15)
            if user32.IsWindowVisible(hwnd):
                log.info("[托盘唤醒] 成功：微信窗口已可见（总耗时 %.2fs）", time.perf_counter() - t0)
                return True
        log.warning("[托盘唤醒] 已点击图标但窗口 %s 在 %.2fs 内仍不可见", hwnd, time.perf_counter() - t0)
        return user32.IsWindowVisible(hwnd)
    except Exception as e:
        log.exception("[托盘唤醒] 异常：%s", e)
        return False

def _wake_via_hotkey(hwnd: int) -> bool:

    log.info("[托盘唤醒] 发送 Ctrl+Alt+W 热键尝试调起微信…")
    try:
        user32 = ctypes.windll.user32
        VK_CONTROL, VK_MENU, VK_W = 0x11, 0x12, 0x57
        KEYUP = 0x0002

        user32.keybd_event(VK_CONTROL, 0, 0, 0)
        user32.keybd_event(VK_MENU, 0, 0, 0)
        user32.keybd_event(VK_W, 0, 0, 0)
        time.sleep(0.05)

        user32.keybd_event(VK_W, 0, KEYUP, 0)
        user32.keybd_event(VK_MENU, 0, KEYUP, 0)
        user32.keybd_event(VK_CONTROL, 0, KEYUP, 0)
        for i in range(10):
            time.sleep(0.15)
            if user32.IsWindowVisible(hwnd):
                log.info("[托盘唤醒] 热键调起成功")
                return True
        log.warning("[托盘唤醒] 热键也未能调起微信")
        return user32.IsWindowVisible(hwnd)
    except Exception as e:
        log.exception("[托盘唤醒] 热键异常：%s", e)
        return False

def _wake_window(hwnd: int) -> bool:

    log.info("[托盘唤醒] 进入唤醒链：先尝试 UIA 点托盘图标…")
    if _wake_via_tray_icon(hwnd):
        log.info("[托盘唤醒] UIA 点托盘图标成功")
        return True
    log.warning("[托盘唤醒] UIA 路径未成功，退回 Ctrl+Alt+W 热键兜底")
    return _wake_via_hotkey(hwnd)

def restore_window(hwnd: int) -> bool:

    user32 = ctypes.windll.user32
    SW_RESTORE = 9
    if user32.IsIconic(hwnd):

        log.info("[托盘唤醒] 微信处最小化态，先尝试 UIA 点托盘图标唤醒")
        if not _wake_via_tray_icon(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
        time.sleep(0.3)
        return True
    if not user32.IsWindowVisible(hwnd):

        log.info("[托盘唤醒] 微信处托盘/隐藏态，开始唤醒流程")
        if _wake_window(hwnd):
            return True
        log.warning(
            "托盘唤醒失败：UIA 点托盘图标与 Ctrl+Alt+W 热键均未能调起微信，"
            "微信仍处托盘态。未执行 ShowWindow（避免半卡死），请手动点击托盘图标恢复微信。"
        )
        return False
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

def activate(control_or_hwnd) -> bool:

    hwnd = None
    try:
        hwnd = int(control_or_hwnd)
    except (TypeError, ValueError):
        try:
            hwnd = control_or_hwnd.NativeWindowHandle
        except Exception:
            pass
    if not hwnd:
        return False
    user32 = ctypes.windll.user32
    try:

        if user32.IsIconic(hwnd):
            if not _wake_via_tray_icon(hwnd):
                user32.ShowWindow(hwnd, 9)
        elif not user32.IsWindowVisible(hwnd):
            _wake_window(hwnd)

        VK_MENU = 0x12
        KEYUP = 0x0002
        try:
            user32.AllowSetForegroundWindow(0xFFFFFFFF)
        except Exception:
            pass

        def _release_lock_and_steal():

            for _ in range(6):
                try:
                    user32.keybd_event(VK_MENU, 0, 0, 0)
                    user32.keybd_event(VK_MENU, 0, KEYUP, 0)
                except Exception:
                    pass
                try:
                    user32.SetForegroundWindow(hwnd)
                    user32.BringWindowToTop(hwnd)
                except Exception:
                    pass
                time.sleep(0.04)
                try:
                    if user32.GetForegroundWindow() == hwnd:
                        return True
                except Exception:
                    pass
            return False

        if _release_lock_and_steal():
            time.sleep(0.12)

            log.debug("[窗口激活] 已置前 hwnd=%s（Alt 释放前台锁，窗口可见态，未点托盘）", hwnd)
            return True

        HWND_TOPMOST, HWND_NOTOPMOST = -1, -2
        SWP_NOMOVE, SWP_NOSIZE = 0x0002, 0x0001
        try:
            user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE)
            time.sleep(0.06)
            user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE)
        except Exception:
            pass
        time.sleep(0.12)
        try:
            return user32.GetForegroundWindow() == hwnd
        except Exception:
            return False
    except Exception:
        return False
