

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import uiautomation as auto
from win import finder

MAX_DEPTH = 14

def dump(node, depth):
    if depth > MAX_DEPTH:
        return
    indent = "  " * depth
    try:
        ctype = node.ControlType
    except Exception:
        ctype = "?"
    try:
        name = node.Name or ""
    except Exception:
        name = ""
    try:
        cls = node.ClassName
    except Exception:
        cls = ""
    try:
        r = node.BoundingRectangle
        size = f"{r.width()}x{r.height()}"
    except Exception:
        size = "?"
    mark = " <== 可能是搜索框" if ("搜索" in name) else ""
    print(f"{indent}[{ctype}] name={name!r} class={cls!r} size={size}{mark}")
    try:
        for child in node.GetChildren():
            dump(child, depth + 1)
    except Exception:
        pass

def main():
    hwnd = finder.find_hwnd()
    if not hwnd:
        print("未找到微信主窗口（标题/类都没匹配到）")
        return
    print(f"找到微信主窗口 hwnd={hwnd}")
    ctrl = finder.reacquire(hwnd) or finder.locate()
    if ctrl is None:
        print("找到窗口句柄但取不到 UIA 控件（窗口可能隐藏/最小化未恢复）")
        return
    print("==== 微信主窗口控件树 ====")
    dump(ctrl, 0)
    print("==== 结束 ====")

    diag = []
    found = finder
    del found
    edit_count = {"n": 0}

    def count_edits(node, depth):
        if depth > MAX_DEPTH:
            return
        try:
            if node.ControlType == auto.ControlType.Edit:
                edit_count["n"] += 1
        except Exception:
            pass
        try:
            for child in node.GetChildren():
                count_edits(child, depth + 1)
        except Exception:
            pass

    count_edits(ctrl, 0)
    print(f"窗口内标准 Edit 控件总数: {edit_count['n']}")

if __name__ == "__main__":
    main()
