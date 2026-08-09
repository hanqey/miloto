

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import uiautomation as auto
from win import finder

MAX_DEPTH = 24

MARK_KEYWORDS = (
    "红包", "redpacket", "luckymoney", "接龙", "chain",
    "表情", "emoji", "sticker", "image", "picture",
)

def _cls(node):
    try:
        return node.ClassName or ""
    except Exception:
        return ""

def _name(node):
    try:
        return node.Name or ""
    except Exception:
        return ""

def _ctype(node):
    try:
        return node.ControlType
    except Exception:
        return "?"

def _size(node):
    try:
        r = node.BoundingRectangle
        return f"{r.width()}x{r.height()}"
    except Exception:
        return "?"

def dump(node, depth, no_content):
    if depth > MAX_DEPTH:
        return
    indent = "  " * depth
    cls = _cls(node)
    name = _name(node)
    ctype = _ctype(node)
    size = _size(node)

    low = (name + cls).lower()
    mark = ""
    if any(k in low for k in MARK_KEYWORDS):
        mark = " <== 疑似图片/红包/接龙/表情"

    if no_content:
        name_disp = ""
    else:

        name_disp = f" name={name!r}"

    print(f"{indent}[{ctype}] class={cls!r}{name_disp} size={size}{mark}")

    try:
        for child in node.GetChildren():
            dump(child, depth + 1, no_content)
    except Exception:
        pass

def main():
    no_content = "--no-content" in sys.argv
    hwnd = finder.find_hwnd()
    if not hwnd:
        print("未找到微信主窗口（标题/类都没匹配到）")
        return
    ctrl = finder.reacquire(hwnd) or finder.locate()
    if ctrl is None:
        print("找到窗口句柄但取不到 UIA 控件（窗口可能隐藏/最小化未恢复）")
        return
    print(f"hwnd={hwnd}  no_content={no_content}")
    print("==== 微信窗口控件树 ====")
    dump(ctrl, 0, no_content)
    print("==== 结束 ====")

if __name__ == "__main__":
    main()
