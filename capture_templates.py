

import os
import time
import ctypes
import ctypes.wintypes

try:
    from PIL import ImageGrab
except Exception:
    ImageGrab = None

SEARCH_BOX_RECT = (0.20, 0.02, 0.20, 0.05)
FIRST_RESULT_RECT = (0.20, 0.075, 0.20, 0.06)

ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "win", "assets")

def _dpi_scale():
    try:
        user32 = ctypes.windll.user32
        logical_w = user32.GetSystemMetrics(0)
        full = ImageGrab.grab()
        device_w = full.size[0]
        if logical_w and device_w:
            return device_w / logical_w
    except Exception:
        pass
    return 1.0

def _find_hwnd():
    user32 = ctypes.windll.user32
    user32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
    user32.FindWindowW.restype = ctypes.c_void_p
    hwnd = user32.FindWindowW(None, "微信")
    return int(hwnd) if hwnd else 0

def main():
    if ImageGrab is None:
        print("请先安装 Pillow：pip install Pillow")
        return
    try:
        import win32gui
    except Exception:
        win32gui = None

    hwnd = _find_hwnd()
    if not hwnd:
        print("未找到微信窗口，请先打开微信主窗口再运行本脚本。")
        return

    user32 = ctypes.windll.user32
    user32.AllowSetForegroundWindow(0xFFFFFFFF)
    if win32gui is not None:
        win32gui.ShowWindow(hwnd, 9)
        win32gui.SetForegroundWindow(hwnd)
    print("已将微信置前，2 秒后截图（请勿遮挡微信窗口）…")
    time.sleep(2.0)

    scale = _dpi_scale()
    rect = ctypes.wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    left, top = int(rect.left * scale), int(rect.top * scale)
    right, bottom = int(rect.right * scale), int(rect.bottom * scale)
    W, H = right - left, bottom - top

    shot = ImageGrab.grab((left, top, right, bottom))
    os.makedirs(ASSETS_DIR, exist_ok=True)
    win_path = os.path.join(ASSETS_DIR, "wechat_window.png")
    shot.save(win_path)
    print(f"整窗截图已存：{win_path}  (窗口尺寸 {W}x{H})")

    def crop(rel, name):
        x, y, w, h = rel
        box = (int(x * W), int(y * H), int((x + w) * W), int((y + h) * H))
        crop_img = shot.crop(box)
        path = os.path.join(ASSETS_DIR, name)
        crop_img.save(path)
        print(f"已裁 {name}: {path}  (相对 {rel})")

    crop(SEARCH_BOX_RECT, "search_box.png")
    crop(FIRST_RESULT_RECT, "first_result.png")
    print("\n校准完成。如自动裁的不准：用图片工具打开 wechat_window.png，")
    print("手动裁出「搜索框」「首条结果行」两张图，覆盖 assets/ 下同名文件即可。")

if __name__ == "__main__":
    main()
