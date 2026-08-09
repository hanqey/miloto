

import asyncio
import threading

class EventBus:

    INBOUND = "inbound"
    PRE_PUSH = "pre_push"
    OUTBOUND = "outbound"
    FILE_DROPPED = "file"
    MEDIA_READY = "media_ready"
    POST_PUSH = "post_push"
    TICK = "tick"

    TRANSFORM_EVENTS = {INBOUND, PRE_PUSH}

    def __init__(self):

        self._subs = {}
        self._lock = threading.Lock()

        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(
            target=self._loop.run_forever, daemon=True, name="bus-loop")
        self._loop_thread.start()

    def subscribe(self, event_type: str, callback, priority: int = 50) -> None:

        with self._lock:
            self._subs.setdefault(event_type, []).append((callback, priority))

    def invoke(self, cb, payload):

        if asyncio.iscoroutinefunction(cb):
            fut = asyncio.run_coroutine_threadsafe(cb(payload), self._loop)
            return fut.result()
        return cb(payload)

    def emit(self, event_type: str, payload):

        subs = sorted(self._subs.get(event_type, []), key=lambda x: x[1])
        if event_type in self.TRANSFORM_EVENTS:
            for cb, _ in subs:
                try:
                    res = self.invoke(cb, payload)
                except Exception as exc:
                    notify_hook_error(cb, event_type, exc)
                    print(f"[bus] 扩展回调异常 ({event_type}): {exc}")
                    continue
                if res is None:
                    return None
                payload = res
            return payload
        for cb, _ in subs:
            try:
                self.invoke(cb, payload)
            except Exception as exc:
                notify_hook_error(cb, event_type, exc)
                print(f"[bus] 扩展回调异常 ({event_type}): {exc}")
        return None

    def clear(self) -> None:

        with self._lock:
            self._subs = {}

    def unsubscribe(self, inst) -> None:

        with self._lock:
            for ev in list(self._subs.keys()):
                self._subs[ev] = [
                    (cb, p) for cb, p in self._subs[ev]
                    if getattr(cb, "__self__", None) is not inst
                ]

def notify_hook_error(cb, hook: str, exc: Exception) -> None:

    inst = getattr(cb, "__self__", None)
    if inst is not None and hasattr(inst, "on_error"):
        try:
            inst.on_error(hook, exc)
        except Exception:
            pass
