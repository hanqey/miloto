

def match_contact(recent, mtime: float, window: float = 30.0) -> str | None:

    best = None
    best_dt = 1e18
    for ts, contact in recent:
        dt = abs(ts - mtime)
        if dt < best_dt and dt <= window:
            best_dt = dt
            best = contact
    return best
