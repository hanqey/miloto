

import re

MILOTO_VERSION = "1.2.0"

def _tup(v: str):

    return tuple(int(x) for x in re.findall(r"\d+", v))

def meets_requirement(req: str) -> bool:

    if not req:
        return True
    m = re.match(r"^(>=|<=|>|<|==|!=)?\s*([0-9]+(?:\.[0-9]+)*)$", req.strip())
    if not m:
        return True
    op = m.group(1) or ">="
    ver = m.group(2)
    a, b = _tup(MILOTO_VERSION), _tup(ver)

    la, lb = len(a), len(b)
    if la < lb:
        a = a + (0,) * (lb - la)
    elif lb < la:
        b = b + (0,) * (la - lb)
    if op == ">=":
        return a >= b
    if op == "<=":
        return a <= b
    if op == ">":
        return a > b
    if op == "<":
        return a < b
    if op == "==":
        return a == b
    if op == "!=":
        return a != b
    return True
