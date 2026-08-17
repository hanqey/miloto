

import ctypes
import math
import random
import time

_user32 = ctypes.windll.user32

class _Point(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

def _current_position():
    p = _Point()
    try:
        _user32.GetCursorPos(ctypes.byref(p))
    except Exception:
        return 0, 0
    return p.x, p.y

def _clamp(v, lo, hi):
    return lo if v < lo else (hi if v > hi else v)

def smooth_move_to(tx: int, ty: int, *, duration: float = 0.30, jitter: bool = True,
                   arc_scale: float = 0.22, min_arc: float = 18.0, max_arc: float = 135.0,
                   overshoot: bool = True) -> None:
    """把鼠标从当前位置沿一段明显弯曲、像手腕甩过去的弧线移动到 (tx, ty)。

    采用三次贝塞尔：C1 负责「鼓包」（垂直方向明显偏移，近距离也保证最小可见弧度），
    C2 负责「末端过冲再回拉」（先越过目标一点、再收回来，是真人收尾的典型动作）。
    途中还会以一定概率在某一步随机「犹豫」一下（微停顿），避免匀速直线的机械感。
    duration 为大致总耗时（秒），实际随机 ±25%；jitter 控制逐帧像素抖动。
    """
    sx, sy = _current_position()
    dx, dy = tx - sx, ty - sy
    dist = math.hypot(dx, dy)

    if dist < 4:
        _user32.SetCursorPos(int(tx), int(ty))
        time.sleep(0.015 + random.uniform(0, 0.02))
        return

    ux, uy = dx / dist, dy / dist
    nx, ny = -uy, ux

    arc_mag = random.uniform(0.7, 1.3) * max(dist * arc_scale, min_arc)
    arc_mag = min(arc_mag, max_arc)
    arc_sign = random.choice((-1, 1))
    c1x = sx + dx * 0.35 + nx * arc_sign * arc_mag
    c1y = sy + dy * 0.35 + ny * arc_sign * arc_mag

    if overshoot:
        osh = random.uniform(10.0, 22.0)
        oang = random.uniform(-0.35, 0.35)
        ox = ux * math.cos(oang) - uy * math.sin(oang)
        oy = ux * math.sin(oang) + uy * math.cos(oang)
        c2x = tx + ox * osh
        c2y = ty + oy * osh
    else:
        c2x = sx + dx * 0.72
        c2y = sy + dy * 0.72

    steps = _clamp(int(dist / 9), 12, 50)
    dur = duration * random.uniform(0.82, 1.28)

    hesitate_step = random.randint(2, max(2, steps - 2)) if random.random() < 0.6 else -1
    start = time.perf_counter()
    for i in range(1, steps + 1):
        t = i / steps
        it = 1.0 - t

        bx = it * it * it * sx + 3.0 * it * it * t * c1x + 3.0 * it * t * t * c2x + t * t * t * tx
        by = it * it * it * sy + 3.0 * it * it * t * c1y + 3.0 * it * t * t * c2y + t * t * t * ty
        if jitter:
            bx += random.uniform(-2.4, 2.4)
            by += random.uniform(-2.4, 2.4)
        _user32.SetCursorPos(int(round(bx)), int(round(by)))
        if i == hesitate_step:
            time.sleep(random.uniform(0.04, 0.11))

        target_elapsed = (t ** 1.4) * dur
        sleep_for = target_elapsed - (time.perf_counter() - start)
        if sleep_for > 0:
            time.sleep(sleep_for)

    _user32.SetCursorPos(int(tx), int(ty))

def human_click(tx: int, ty: int, *, move: bool = True, curved: bool = True,
                before: float = 0.0, hold: float = 0.07, after: float = 0.06,
                jitter: bool = True) -> bool:
    """拟人单击：(tx, ty) 为屏幕坐标。

    - before：出手前停顿（额外随机 0~40ms），模拟「先想一下再动手」；
    - 移动：curved=True 走弧线（默认带过冲与犹豫），否则直线瞬移；
    - 落点前预判停顿 ~20~50ms，再按下；
    - 按下保持 hold（额外随机 0~20ms），再抬起；
    - after：抬手后余顿（额外随机 0~30ms），模拟点击收尾。
    返回 True 表示动作已发出（不验证窗口是否真的响应）。
    """
    try:
        if before > 0:
            time.sleep(before + random.uniform(0, 0.04))
        if move:
            if curved:
                smooth_move_to(tx, ty, jitter=jitter)
            else:
                _user32.SetCursorPos(int(tx), int(ty))
                time.sleep(0.02 + random.uniform(0, 0.02))
        else:
            _user32.SetCursorPos(int(tx), int(ty))

        time.sleep(0.02 + random.uniform(0, 0.03))
        _user32.mouse_event(0x0002, 0, 0, 0, 0)
        time.sleep(hold + random.uniform(0, 0.02))
        _user32.mouse_event(0x0004, 0, 0, 0, 0)
        if after > 0:
            time.sleep(after + random.uniform(0, 0.03))
        return True
    except Exception:
        return False

def human_hover(tx: int, ty: int, *, curved: bool = True, jitter: bool = True) -> None:
    """仅移动鼠标到 (tx, ty)，不点击（部分场景只需悬停）。"""
    if curved:
        smooth_move_to(tx, ty, jitter=jitter)
    else:
        _user32.SetCursorPos(int(tx), int(ty))
