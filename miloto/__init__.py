

from core.extension import (
    Extension as Star,
    Context,
    BlockedSender,
    wire_hooks,
    HOOK_EVENTS,
)
from core.loggingx import build_logger
from core.version import MILOTO_VERSION as __version__, meets_requirement as supports_version

logger = build_logger("miloto.plugin")

def _hook_decorator(event_key: str):

    def mark(func):
        func._miloto_hook = event_key
        return func
    return mark

on_inbound = _hook_decorator("inbound")
on_pre_push = _hook_decorator("pre_push")
on_outbound = _hook_decorator("outbound")
on_media_ready = _hook_decorator("media_ready")
on_post_push = _hook_decorator("post_push")
on_file = _hook_decorator("file_dropped")
on_tick = _hook_decorator("tick")

def register(name=None, version=None, author=None, desc=None,
             miloto_version=None, deps=None):
    """插件元数据装饰器（仿 astrbot 的 @register / metadata.yaml）。

    装饰在 Star 子类上，等价于在 manifest.yaml 写元数据；加载器优先用
    manifest.yaml，缺失字段时回退到此处设置的类属性。
    """
    def decorate(cls):
        if name:
            cls.name = name
        if version:
            cls.version = version
        if author:
            cls.author = author
        if desc:
            cls.desc = desc
        if miloto_version:
            cls.miloto_version = miloto_version
        if deps:
            cls.deps = deps
        return cls
    return decorate

__all__ = [
    "Star", "Context", "BlockedSender", "wire_hooks", "HOOK_EVENTS",
    "logger", "__version__", "supports_version",
    "on_inbound", "on_pre_push", "on_outbound", "on_media_ready",
    "on_post_push", "on_file", "on_tick", "register",
]
