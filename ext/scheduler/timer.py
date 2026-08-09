

import re

def parse_when(s: str):
    s = (s or "").strip().lower()
    if not s:
        return 3600

    m = re.match(r"^(?:every\s+)?(\d+)\s*(s|sec|秒|m|min|分|h|hour|小时)$", s)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        mult = {"s": 1, "sec": 1, "秒": 1,
                "m": 60, "min": 60, "分": 60,
                "h": 3600, "hour": 3600, "小时": 3600}[unit]
        return n * mult

    m = re.match(r"^(\d{1,2}):(\d{2})$", s)
    if m:
        return ("daily", int(m.group(1)), int(m.group(2)))

    return 3600
