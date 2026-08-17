

from __future__ import annotations

import ctypes
import logging

log = logging.getLogger("miloto.vrec.tray")

MAIN_TRAY_CLASSES = {"Shell_TrayWnd", "Shell_SecondaryTrayWnd"}
OVERFLOW_TRAY_CLASSES = {
    "NotifyIconOverflowWindow",
    "TopLevelWindowForOverflowXamlIsland",
}

def _visible_tray_handles(area: str):

    if area == "main":
        wanted = MAIN_TRAY_CLASSES
    elif area == "overflow":
        wanted = OVERFLOW_TRAY_CLASSES
    else:
        return []
    user32 = ctypes.windll.user32
    try:
        user32.GetClassNameW.argtypes = (
            ctypes.wintypes.HWND,
            ctypes.wintypes.LPWSTR,
            ctypes.c_int,
        )
        user32.GetClassNameW.restype = ctypes.c_int
    except Exception:
        pass
    handles = []
    WNDENUMPROC = ctypes.WINFUNCTYPE(
        ctypes.wintypes.BOOL, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM
    )

    def _cb(h, _l):
        try:
            buf = ctypes.create_unicode_buffer(256)
            n = int(user32.GetClassNameW(h, buf, len(buf)))
            cls = buf.value[:n] if n else ""
            if cls in wanted and user32.IsWindowVisible(h):
                handles.append(int(h))
        except Exception:
            pass
        return True

    try:
        user32.EnumWindows.argtypes = (WNDENUMPROC, ctypes.wintypes.LPARAM)
        user32.EnumWindows.restype = ctypes.wintypes.BOOL
        user32.EnumWindows(WNDENUMPROC(_cb), 0)
    except Exception:
        pass
    return sorted(set(handles))

def _is_wechat_name(name) -> bool:

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
        after = folded[len(prefix) : len(prefix) + 1]
        if folded.startswith(prefix) and after in {" ", "-", "–", "—", ":", "("}:
            return True
    return False

def _is_overflow_button(name) -> bool:
    compact = " ".join(str(name or "").split()).strip().casefold()
    return (
        "显示隐藏的图标" in compact
        or "显示隐藏图标" in compact
        or "show hidden icons" in compact
        or "show hidden icon" in compact
    )

def comtypes_scan_tray(area: str):
    """用 comtypes 原生 UIA 枚举通知区子树，返回 [(name, (l,t,r,b)), ...]。

    这是 wechat-bridge 在 Win11 XAML 托盘上能稳定定位微信图标的核心。
    返回坐标单位为屏幕像素（与真实鼠标点击一致）。
    """
    if area not in ("main", "overflow"):
        return []
    try:
        import comtypes
        import comtypes.client

        comtypes.client.GetModule("UIAutomationCore.dll")
        from comtypes.gen import UIAutomationClient
    except Exception as e:
        log.warning("[托盘扫描] comtypes / UIAutomationCore 初始化失败：%s", e)
        return []
    nodes = []
    try:
        comtypes.CoInitialize()
    except Exception:

        pass
    try:
        automation = comtypes.client.CreateObject(
            UIAutomationClient.CUIAutomation,
            interface=UIAutomationClient.IUIAutomation,
        )
        condition = automation.CreateTrueCondition()
        for handle in _visible_tray_handles(area):
            try:
                root = automation.ElementFromHandle(int(handle))
                elements = root.FindAll(
                    UIAutomationClient.TreeScope_Subtree, condition
                )
                count = min(int(elements.Length), 2000)
                for i in range(count):
                    try:
                        el = elements.GetElement(i)
                        name = (el.CurrentName or "").strip()
                        if not name or bool(el.CurrentIsOffscreen):
                            continue
                        r = el.CurrentBoundingRectangle
                        left, top, right, bottom = r.left, r.top, r.right, r.bottom
                        if right - left < 3 or bottom - top < 3:
                            continue
                        nodes.append((name, (left, top, right, bottom)))
                    except Exception:
                        continue
            except Exception:
                continue
    except Exception as e:
        log.warning("[托盘扫描] 区域 %s 枚举失败：%s", area, e)
    log.debug("[托盘扫描] 区域 %s 共扫到 %d 个可见节点", area, len(nodes))
    return nodes
