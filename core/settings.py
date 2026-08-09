

import os
import sys
import yaml

RENAMED = {
    "dashboard_username": "dashboard.username",
    "dashboard_password": "dashboard.password",
    "web_host": "web.host",
    "web_port": "web.port",
    "bot_names": "bot.names",
    "bot_wxid": "bot.wxid",
    "vision_url": "vision.url",
    "vision_key": "vision.key",
    "vision_model": "vision.model",
    "ollama_url": "ollama.url",
    "ollama_timeout": "ollama.timeout",
    "wechat_image_dir": "wechat.image_dir",
    "wechat_file_dir": "wechat.file_dir",
    "auto_original": "wechat.auto_original",
    "auto_open_to_download": "wechat.auto_open_to_download",
    "file_storage_dir": "files.storage_dir",
    "download_dir": "files.download_dir",
    "file_whitelist": "files.whitelist",
    "file_server_port": "files.server_port",
    "ob_connections": "connections.onebot",
    "weflow_connections": "connections.weflow",
    "buffer_secs": "buffer_seconds",
    "extensions": "enabled_plugins",
}

def read_path(data: dict, path: str, default=None):

    current = data
    for step in path.split("."):
        if not isinstance(current, dict) or step not in current:
            return default
        current = current[step]
    return current

def write_path(data: dict, path: str, value) -> None:

    steps = path.split(".")
    current = data
    for step in steps[:-1]:
        nxt = current.get(step)
        if not isinstance(nxt, dict):
            nxt = {}
            current[step] = nxt
        current = nxt
    current[steps[-1]] = value

def expand_paths(flat: dict) -> dict:

    expanded = {}
    for key, value in (flat or {}).items():
        if isinstance(key, str) and "." in key:
            write_path(expanded, key, value)
        else:
            expanded[key] = value
    return expanded

def modernize(data: dict) -> bool:

    changed = False
    for old_key, new_path in RENAMED.items():
        if old_key not in data:
            continue
        value = data.pop(old_key)
        if read_path(data, new_path, _MISSING) is _MISSING:
            write_path(data, new_path, value)
        changed = True
    return changed

_MISSING = object()

class Settings:
    def __init__(self, data: dict):

        self.data = data or {}

    def get(self, key, default=None):

        value = read_path(self.data, key, _MISSING)
        if value is not _MISSING:
            return value
        if key in RENAMED:
            value = read_path(self.data, RENAMED[key], _MISSING)
            if value is not _MISSING:
                return value
        return default

    def _pick(self, path: str, default=None):

        value = read_path(self.data, path, _MISSING)
        if value is not _MISSING:
            return value
        for old_key, new_path in RENAMED.items():
            if new_path == path and old_key in self.data:
                return self.data[old_key]
        return default

    @property
    def onebot_connections(self) -> list:

        connections = self._pick("connections.onebot")
        if isinstance(connections, list) and connections:
            return connections
        url = self.get("onebot.url") or self.get("ob_url")
        if url:
            return [{"name": "AstrBot", "url": url,
                     "token": self.get("ob_token", ""), "enable": True}]
        return []

    @property
    def weflow_connections(self) -> list:

        connections = self._pick("connections.weflow")
        if isinstance(connections, list) and connections:
            return connections
        url = self.get("weflow.url") or self.get("weflow_url")
        if url:
            return [{"name": "WeFlow", "url": url,
                     "token": self.get("token", ""), "enable": True}]
        return []

    @property
    def onebot_url(self) -> str:
        connections = self.onebot_connections
        return connections[0]["url"] if connections else "ws://127.0.0.1:19777"

    @property
    def onebot_token(self) -> str:
        connections = self.onebot_connections
        return connections[0].get("token", "") if connections else ""

    @property
    def weflow_url(self) -> str:
        connections = self.weflow_connections
        return connections[0]["url"] if connections else "http://127.0.0.1:5031"

    @property
    def weflow_token(self) -> str:
        connections = self.weflow_connections
        return connections[0].get("token", "") if connections else ""

    @property
    def bot_names(self) -> list:

        return self._pick("bot.names", [])

    @property
    def bot_wxid(self) -> str:
        return self._pick("bot.wxid", "")

    @property
    def web_host(self) -> str:
        return self._pick("web.host", "0.0.0.0")

    @property
    def web_port(self) -> int:
        return int(self._pick("web.port", 8127))

    @property
    def dashboard(self) -> dict:

        return {
            "username": self._pick("dashboard.username") or "miloto",
            "password": self._pick("dashboard.password") or "",
        }

    @property
    def buffer_seconds(self) -> float:

        return float(self._pick("buffer_seconds", 5))

    @property
    def attachments(self) -> str:

        return self.data.get("attachments", "")

    @property
    def enabled_plugins(self) -> list:

        return self._pick("enabled_plugins", [])

    @property
    def plugins(self) -> dict:

        return self.data.get("plugins", {}) or {}

    @property
    def plugin_market_url(self) -> str:

        return self._pick("plugins.market_url", "https://raw.githubusercontent.com/hanqey/miloto/main/market/market.json")

    @property
    def log_levels(self) -> dict:

        levels = {"debug": False, "info": True, "warning": True, "error": True}
        configured = self.data.get("log_levels")
        if isinstance(configured, dict):
            for name in levels:
                if name in configured:
                    levels[name] = bool(configured[name])
        return levels

    @property
    def debug(self) -> bool:

        return bool(self.log_levels.get("debug", self.data.get("debug", False)))

    @property
    def wechat_image_dir(self) -> str:
        return self._pick("wechat.image_dir", "")

    @property
    def wechat_file_dir(self) -> str:

        return self._pick("wechat.file_dir", "")

    @property
    def auto_original(self) -> bool:

        return bool(self._pick("wechat.auto_original", False))

    @property
    def auto_open_to_download(self) -> bool:

        return bool(self._pick("wechat.auto_open_to_download", True))

    @property
    def file_storage_dir(self) -> str:

        return self._pick("files.storage_dir", "")

    @property
    def download_dir(self) -> str:
        return self._pick("files.download_dir", "")

    @property
    def file_whitelist(self) -> list:
        return self._pick("files.whitelist", [])

    @property
    def file_server_port(self) -> int:
        try:
            return int(self._pick("files.server_port", 8777))
        except (TypeError, ValueError):
            return 8777

    @property
    def vision_url(self) -> str:
        return self._pick("vision.url", "")

    @property
    def vision_key(self) -> str:
        return self._pick("vision.key", "")

    @property
    def vision_model(self) -> str:
        return self._pick("vision.model", "")

    @property
    def ollama_url(self) -> str:
        return self._pick("ollama.url", "http://127.0.0.1:61000")

    @property
    def ollama_timeout(self) -> int:
        return int(self._pick("ollama.timeout", 60))

    @property
    def scheduler(self) -> list:
        return self.data.get("scheduler", [])

    @property
    def commands(self) -> list:
        return self.data.get("commands", [])

def default_config_path() -> str:

    if getattr(sys, "frozen", False):
        root = os.path.dirname(sys.executable)
    else:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "config.yaml")

def load_settings(path: str = None) -> Settings:

    if path is None:
        path = default_config_path()
    if not os.path.exists(path):
        return Settings({})
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return Settings(data)
