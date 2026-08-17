

import os
import time
import ctypes
import ctypes.wintypes

from core.loggingx import build_logger

log = build_logger("miloto.visual")

_VREC_DIR = os.path.dirname(os.path.abspath(__file__))
_LOCATORS_DIR = os.path.join(_VREC_DIR, "vrec", "locators")
_SEARCH_JSON = os.path.join(_LOCATORS_DIR, "search_box_anchors.json")
_PRIMARY_JSON = os.path.join(_LOCATORS_DIR, "search_primary_result.json")

RESULT_WAIT_TIMEOUT = 2.5
RESULT_POLL_INTERVAL = 0.15

OPEN_WAIT = 1.2

_ENGINE = None

def _load_engine():
    """加载 vrec 识别引擎。成功返回元组，失败置为 False（后续直接降级）。"""
    global _ENGINE
    if _ENGINE is not None:
        return _ENGINE
    try:
        from win.vrec.relative_locator import RelativeLocator, load_relative_locator
        from win.vrec.derived_locator import DerivedLocator, load_derived_locator
        from PIL import Image, ImageGrab

        search_locator = RelativeLocator(load_relative_locator(_SEARCH_JSON))
        primary_locator = DerivedLocator(load_derived_locator(_PRIMARY_JSON))
        _ENGINE = (search_locator, primary_locator, Image, ImageGrab)
        log.info("[视觉切换] 引擎加载成功")
    except Exception as exc:
        log.warning("[视觉切换] 引擎加载失败，将退回键盘方案：%s", exc)
        _ENGINE = False
    return _ENGINE

def vision_available() -> bool:
    """视觉方案是否可用（引擎与模板资源齐备）。供 typist 在切会话前预判。"""
    engine = _load_engine()
    return isinstance(engine, tuple)

def _window_rect_device(hwnd: int):

    user32 = ctypes.windll.user32
    rect = ctypes.wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    return (rect.left, rect.top, rect.right, rect.bottom)

def _grab_window_image(hwnd):

    from PIL import ImageGrab
    r = _window_rect_device(hwnd)
    if not r:
        return None
    left, top, right, bottom = r
    if right <= left or bottom <= top:
        return None
    try:
        return ImageGrab.grab((left, top, right, bottom)).convert("RGB")
    except Exception as exc:
        log.warning("[视觉切换] 截图失败：%s", exc)
        return None

def _detect_dpi_scale(hwnd: int) -> float:

    try:
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32

        get_dpi_system = getattr(user32, "GetDpiForSystem", None)
        if get_dpi_system is not None:
            try:
                get_dpi_system.argtypes = []
                get_dpi_system.restype = ctypes.c_uint
                raw = get_dpi_system()
                if raw and 0 < raw < 1000:
                    return max(0.5, min(3.0, raw / 96.0))
            except Exception:
                pass

        try:
            dc = user32.GetDC(0)
            if dc:
                dpi = gdi32.GetDeviceCaps(dc, 88)
                user32.ReleaseDC(0, dc)
                if dpi:
                    return max(0.5, min(3.0, dpi / 96.0))
        except Exception:
            pass
    except Exception:
        pass
    return 1.0

def _apply_scale_policy(engine, hwnd: int) -> None:

    try:
        search_locator, primary_locator, _, _ = engine
    except Exception:
        return
    scale = _detect_dpi_scale(hwnd)
    preferred = [round(scale, 4)]
    fallback = [1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0]
    try:
        search_locator.set_scale_policy(preferred, fallback)
        primary_locator.set_scale_policy(preferred, fallback)
        log.debug("[视觉切换] 已按系统缩放校准 scale=%.2fx（首选档）", scale)
    except Exception:
        pass

def _click_screen(sx: int, sy: int) -> bool:

    from win import human_input
    try:
        return human_input.human_click(sx, sy, before=0.05, hold=0.06, after=0.05)
    except Exception:
        return False

def _click_bounds(window_left: int, window_top: int, bounds) -> bool:

    cx = window_left + (bounds.left + bounds.right) // 2
    cy = window_top + (bounds.top + bounds.bottom) // 2
    return _click_screen(cx, cy)

def _paste_via_clipboard(text: str) -> bool:

    from win import clipboard
    return clipboard.paste_text(text)

def _clear_search_box() -> None:

    user32 = ctypes.windll.user32
    VK_CONTROL = 0x11
    VK_A = 0x41
    VK_DELETE = 0x2E
    user32.keybd_event(VK_CONTROL, 0, 0, 0)
    time.sleep(0.02)
    user32.keybd_event(VK_A, 0, 0, 0)
    time.sleep(0.02)
    user32.keybd_event(VK_A, 0, 0x0002, 0)
    time.sleep(0.02)
    user32.keybd_event(VK_CONTROL, 0, 0x0002, 0)
    time.sleep(0.05)
    user32.keybd_event(VK_DELETE, 0, 0, 0)
    time.sleep(0.05)
    user32.keybd_event(VK_DELETE, 0, 0x0002, 0)
    for vk in (VK_CONTROL, 0x12, 0x10, 0x5B, 0x5C):
        try:
            user32.keybd_event(vk, 0, 0x0002, 0)
        except Exception:
            pass

def switch_contact_visual(contact: str, hwnd: int) -> bool:
    """视觉切会话主路径。成功返回 True；任何缺失/失败返回 False（由 typist 退回键盘）。"""
    engine = _load_engine()
    if not isinstance(engine, tuple):
        log.debug("[视觉切换] 引擎不可用，退回键盘方案")
        return False
    if not hwnd:
        return False

    search_locator, primary_locator, Image, ImageGrab = engine
    t0 = time.perf_counter()
    log.info("[视觉切换] 开始：contact=%s hwnd=%s", contact, hwnd)

    try:
        from win import finder
        fg_ok = finder.activate(hwnd)
        if not fg_ok:
            log.warning("[视觉切换] 初始置前未成功（前台锁），改走置顶兜底重试")
            for _ in range(4):
                fg_ok = finder.activate(hwnd)
                if fg_ok:
                    break
                time.sleep(0.2)
        if not fg_ok:
            log.warning("[视觉切换] 始终未能置前微信，退回键盘（避免截到错误窗口）")
            return False
        time.sleep(0.25)
    except Exception:
        return False

    rect = _window_rect_device(hwnd)
    if rect is None:
        log.warning("[视觉切换] 无法取得窗口坐标，退回键盘")
        return False
    win_left, win_top, _, _ = rect

    image = _grab_window_image(hwnd)
    if image is None:
        log.warning("[视觉切换] 无法截取微信窗口，退回键盘")
        return False

    _apply_scale_policy(engine, hwnd)

    search_result = search_locator.locate(image)
    if not search_result.accepted or search_result.click_bounds is None:
        log.warning(
            "[视觉切换] 未定位到搜索框（拒绝原因=%s，锚点分数=%s），退回键盘",
            search_result.failure_code,
            {k: round(v.score, 3) for k, v in search_result.detections.items()},
        )
        return False
    log.debug(
        "[视觉切换] 搜索框命中（方案=%s，点击区=%s）",
        search_result.alternative_id,
        search_result.click_bounds,
    )
    if not _click_bounds(win_left, win_top, search_result.click_bounds):
        log.warning("[视觉切换] 点击搜索框失败，退回键盘")
        return False
    time.sleep(0.15)

    _clear_search_box()
    time.sleep(0.1)
    if not _paste_via_clipboard(contact):
        log.warning("[视觉切换] 粘贴昵称失败，退回键盘")
        return False
    log.info("[视觉切换] 已粘贴昵称，等待搜索结果…")
    time.sleep(0.15)

    primary = None
    waited = 0.0
    while waited < RESULT_WAIT_TIMEOUT:
        time.sleep(RESULT_POLL_INTERVAL)
        waited += RESULT_POLL_INTERVAL
        frame = _grab_window_image(hwnd)
        if frame is None:
            continue
        candidate = primary_locator.locate(frame)
        if candidate.accepted and candidate.click_bounds is not None:
            primary = candidate
            break

    if primary is None:
        log.warning("[视觉切换] 超时未定位到首条结果，退回键盘")
        return False
    log.debug("[视觉切换] 首条结果命中（点击区=%s）", primary.click_bounds)
    if not _click_bounds(win_left, win_top, primary.click_bounds):
        log.warning("[视觉切换] 点击首条结果失败，退回键盘")
        return False

    time.sleep(OPEN_WAIT)
    log.info("[视觉切换] 完成（耗时 %.2fs），未使用回车", time.perf_counter() - t0)
    return True
