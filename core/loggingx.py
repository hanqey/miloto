

import logging
from collections import deque

_LEVEL_STATE = {"DEBUG": False, "INFO": True, "WARNING": True, "ERROR": True}

_LOG_BUFFER = deque(maxlen=1000)

class _LevelFilter:

    def filter(self, record):
        name = record.levelname
        if name == "CRITICAL":
            name = "ERROR"
        return _LEVEL_STATE.get(name, True)

_LEVEL_FILTER = _LevelFilter()

def set_level_enabled(level: str, on: bool) -> None:

    lv = str(level).upper()
    if lv in _LEVEL_STATE:
        _LEVEL_STATE[lv] = bool(on)

def set_levels(state: dict) -> None:

    for k, v in (state or {}).items():
        set_level_enabled(k, v)

def get_levels() -> dict:

    return dict(_LEVEL_STATE)

def set_debug(on: bool) -> None:

    d = bool(on)
    set_levels({"DEBUG": d, "INFO": True, "WARNING": True, "ERROR": True})

def is_debug() -> bool:

    return _LEVEL_STATE.get("DEBUG", False)

def get_log_lines(n: int = 1000) -> list:

    out = []
    for lvl, line in _LOG_BUFFER:
        name = "ERROR" if lvl == "CRITICAL" else lvl
        if _LEVEL_STATE.get(name, True):
            out.append(line)
    return out[-n:]

class _RingBufferHandler(logging.Handler):

    def emit(self, record):
        try:
            _LOG_BUFFER.append((record.levelname, self.format(record)))
        except Exception:
            pass

_ROOT_DONE = False

def _configure_root():

    global _ROOT_DONE
    if _ROOT_DONE:
        return
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    sh.addFilter(_LEVEL_FILTER)
    rb = _RingBufferHandler()
    rb.setFormatter(fmt)
    rb.addFilter(_LEVEL_FILTER)
    root.addHandler(sh)
    root.addHandler(rb)
    _ROOT_DONE = True

def build_logger(name: str) -> logging.Logger:

    _configure_root()
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    return logger
