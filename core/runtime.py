

import hashlib
import threading

running = False
run_lock = threading.Lock()
paused = threading.Event()
paused.clear()

self_id = 0
contact_map = {}

def wxid_to_int(wxid: str) -> int:

    digest = hashlib.sha256(wxid.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big")
