

class Extension:

    name = "unnamed"

    priority = 50

    def __init_subclass__(cls, **kwargs):

        super().__init_subclass__(**kwargs)
        hooks = {}
        parent = cls.__mro__[1] if len(cls.__mro__) > 1 else None
        if parent is not None and getattr(parent, "_hook_methods", None):
            hooks.update(parent._hook_methods)
        for name, value in vars(cls).items():
            if callable(value) and getattr(value, "_miloto_hook", None):
                hooks[value._miloto_hook] = name
        cls._hook_methods = hooks

    log = None

    ctx = None

    def on_load(self, ctx) -> None:

        pass

    def on_unload(self) -> None:

        pass

    def on_inbound(self, msg):

        return msg

    def on_pre_push(self, event):

        return event

    def on_outbound(self, req):

        return None

    def on_media_ready(self, media) -> None:

        pass

    def on_post_push(self, event) -> None:

        pass

    def on_file(self, path: str, meta: dict) -> None:

        pass

    def on_tick(self, ts=None) -> None:

        pass

    def log_info(self, *args, **kwargs):
        if self.log is not None:
            self.log.info(*args, **kwargs)
        else:
            print("[plugin]", *args)

    def log_warning(self, *args, **kwargs):
        if self.log is not None:
            self.log.warning(*args, **kwargs)
        else:
            print("[plugin][warn]", *args)

    def log_error(self, *args, **kwargs):
        if self.log is not None:
            self.log.error(*args, **kwargs)
        else:
            print("[plugin][error]", *args)

    def log_debug(self, *args, **kwargs):
        if self.log is not None:
            self.log.debug(*args, **kwargs)
        else:
            print("[plugin][debug]", *args)

    def on_config_change(self, cfg: dict) -> None:

        pass

    def on_error(self, hook: str, exc: Exception) -> None:

        pass

    def on_all_loaded(self) -> None:

        pass

    def read_own_config(self, default=None):

        import json as _json
        import os as _os
        p = getattr(self, "_local_config_path", None)
        if not p or not _os.path.exists(p):
            return default
        try:
            with open(p, "r", encoding="utf-8") as fh:
                return _json.load(fh)
        except Exception:
            return default

    def save_own_config(self, data) -> bool:

        import json as _json
        import os as _os
        p = getattr(self, "_local_config_path", None)
        if not p:
            return False
        try:
            d = _os.path.dirname(p)
            if d and not _os.path.isdir(d):
                _os.makedirs(d, exist_ok=True)
            existing = {}
            if _os.path.exists(p):
                try:
                    with open(p, "r", encoding="utf-8") as fh:
                        existing = _json.load(fh) or {}
                except Exception:
                    existing = {}
            if not isinstance(existing, dict):
                existing = {}
            if isinstance(data, dict):
                existing.update(data)
            else:
                existing = data
            with open(p, "w", encoding="utf-8") as fh:
                _json.dump(existing, fh, ensure_ascii=False, indent=2)
            return True
        except Exception:
            return False

class BlockedSender:

    def __init__(self, label):
        self._label = label

    def __getattr__(self, name):
        raise RuntimeError(
            f"插件【{self._label}】不允许直接发送：框架约定插件只能修改转发内容，"
            f"不能改变发送方式（sender/pusher 已被禁用）。需要主动发送请用内置扩展机制。"
        )

class Context:
    def __init__(self, bus, settings, sender, pusher, logger,
                 config=None, extensions=None):
        self.bus = bus
        self.settings = settings
        self.sender = sender
        self.pusher = pusher
        self.log = logger
        self.config = config or {}
        self.extensions = extensions or {}

HOOK_EVENTS = {
    "on_inbound": "INBOUND",
    "on_pre_push": "PRE_PUSH",
    "on_media_ready": "MEDIA_READY",
    "on_post_push": "POST_PUSH",
    "on_file": "FILE_DROPPED",
    "on_tick": "TICK",
    "on_outbound": "OUTBOUND",
}

HOOK_BUS_ATTR = {
    "inbound": "INBOUND",
    "pre_push": "PRE_PUSH",
    "outbound": "OUTBOUND",
    "media_ready": "MEDIA_READY",
    "post_push": "POST_PUSH",
    "file_dropped": "FILE_DROPPED",
    "tick": "TICK",
}

def wire_hooks(bus, inst) -> None:

    cls = inst.__class__
    prio = getattr(inst, "priority", 50)
    decorated = dict(getattr(cls, "_hook_methods", {}) or {})
    for event_key, meth in decorated.items():
        attr = HOOK_BUS_ATTR.get(event_key)
        if attr:
            bus.subscribe(getattr(bus, attr), getattr(inst, meth), priority=prio)
    for meth, attr in HOOK_EVENTS.items():
        if meth in decorated.values():
            continue
        if getattr(cls, meth) is not getattr(Extension, meth):
            bus.subscribe(getattr(bus, attr), getattr(inst, meth), priority=prio)
