

import os
import sys
import time
import json
import asyncio
import threading
import importlib

import yaml

from core.loggingx import build_logger, set_levels, set_level_enabled
from core.settings import (load_settings, read_path, write_path, expand_paths,
                           modernize, default_config_path)
from core import runtime
from core.bus import EventBus
from core.extension import Context, Extension, BlockedSender, wire_hooks
from core.version import meets_requirement, MILOTO_VERSION
from core.relay import InboundRelay
from core.outbound import OutboundHandler
from core.runtime import contact_map, wxid_to_int
from net.weflow_client import WeFlowClient
from net.onebot_client import OneBotClient
from net.onebot_proto import build_api_response
from win.typist import WeChatSender
import miloto

log = build_logger("miloto.engine")

SEND_ACTIONS = ("send_msg", "send_private_msg", "send_group_msg")
TICK_INTERVAL = 5

def find_plugin_class(mod):

    for attr in dir(mod):
        obj = getattr(mod, attr)
        if (isinstance(obj, type) and issubclass(obj, Extension)
                and obj is not Extension and obj.__module__ == mod.__name__):
            return obj
    return None

def plugins_directory() -> str:

    if getattr(sys, "frozen", False):
        return os.path.join(os.path.dirname(sys.executable), "plugins")
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugins")

def build_plugin_context(ctx, name, plugin_config) -> Context:

    return Context(bus=ctx.bus, settings=ctx.settings, sender=BlockedSender(name),
                   pusher=BlockedSender(name), logger=ctx.log,
                   config=plugin_config or {}, extensions=ctx.extensions)

def load_extensions(settings, ctx) -> dict:

    loaded = {}
    builtin_names = set()

    for name in settings.enabled_plugins:
        builtin_names.add(name)
        try:
            module = importlib.import_module(f"ext.{name}")
        except Exception as error:
            log.error(f"加载扩展失败: {name} -> {error}")
            continue
        plugin_class = find_plugin_class(module)
        if plugin_class is None:
            log.error(f"扩展 {name} 未找到 Extension 子类")
            continue
        try:
            loaded[name] = plugin_class()
        except Exception as error:
            log.error(f"实例化扩展失败: {name} -> {error}")

    all_plugin_configs = settings.plugins or {}
    plugins_dir = plugins_directory()

    discovered = []
    if os.path.isdir(plugins_dir):
        for entry in sorted(os.listdir(plugins_dir)):
            full_path = os.path.join(plugins_dir, entry)
            if os.path.isdir(full_path):
                if os.path.exists(os.path.join(full_path, "__init__.py")):
                    discovered.append(entry)
            elif entry.endswith(".py") and not entry.startswith("_"):
                discovered.append(entry[:-3])

    for plugin_name in sorted(set(discovered)):
        plugin_config = all_plugin_configs.get(plugin_name, {})
        turned_off = (isinstance(plugin_config, dict)
                      and plugin_config.get("enabled", True) is False)
        if turned_off:
            continue
        try:
            plugin, _manifest = load_plugin_module(
                plugin_name, plugins_dir, ctx, plugin_config)
        except Exception as error:

            log.error(f"插件加载异常: {plugin_name} -> {error}")
            continue
        if plugin is not None:
            loaded[plugin_name] = plugin

    ctx.extensions = loaded
    for name, plugin in loaded.items():
        plugin_ctx = ctx if name in builtin_names else build_plugin_context(
            ctx, name, all_plugin_configs.get(name, {}))
        plugin.ctx = plugin_ctx
        plugin.log = plugin_ctx.log
        try:
            plugin.on_load(plugin_ctx)
        except Exception as error:
            log.error(f"扩展 on_load 失败: {name} -> {error}")
        wire_hooks(ctx.bus, plugin)

    for name, plugin in loaded.items():
        try:
            plugin.on_all_loaded()
        except Exception as error:
            log.warning(f"扩展 on_all_loaded 异常: {name} -> {error}")
    return loaded

def load_plugin_module(plugin_name: str, plugins_dir: str, ctx, plugin_config):

    import importlib.util

    folder = os.path.join(plugins_dir, plugin_name)
    is_folder = os.path.isdir(folder)

    candidates = []
    if is_folder:
        candidates.append(os.path.join(folder, "__init__.py"))
        candidates.append(os.path.join(folder, f"{plugin_name}.py"))
    candidates.append(os.path.join(plugins_dir, f"{plugin_name}.py"))
    entry_file = next((p for p in candidates if os.path.exists(p)), None)
    if entry_file is None:
        log.error(f"插件未找到: {plugin_name}（应在 {plugins_dir}）")
        return None, None

    plugin_dir = folder if is_folder else plugins_dir
    own_config_path = os.path.join(
        plugin_dir, "config.json" if is_folder else f"{plugin_name}.config.json")

    manifest = {}
    manifest_path = os.path.join(plugin_dir, "manifest.yaml")
    if os.path.exists(manifest_path):
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = yaml.safe_load(f) or {}
        except Exception as error:
            log.warning(f"插件 {plugin_name} manifest 解析失败: {error}")

    required_version = manifest.get("miloto_version")
    if required_version and not meets_requirement(required_version):
        log.error(f"插件 {plugin_name} 要求的 miloto_version {required_version} "
                  f"不满足当前 {MILOTO_VERSION}，已禁用")
        return None, manifest

    try:
        spec = importlib.util.spec_from_file_location(
            f"miloto_plugin_{plugin_name}", entry_file)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except Exception as error:
        log.error(f"加载插件失败: {plugin_name} -> {error}")
        return None, manifest

    plugin_class = find_plugin_class(module)
    if plugin_class is None:
        log.error(f"插件 {plugin_name} 未找到 Extension 子类")
        return None, manifest
    try:
        plugin = plugin_class()
    except Exception as error:
        log.error(f"实例化插件失败: {plugin_name} -> {error}")
        return None, manifest

    info = dict(manifest)
    for key in ("name", "version", "author", "desc", "description"):
        value = getattr(plugin_class, key, None)
        if value and value != "unnamed" and key not in info:
            info[key] = value

    plugin._manifest = info
    plugin._loaded = True
    plugin.name = info.get("name") or plugin_name
    plugin._plugin_dir = plugin_dir
    plugin._local_config_path = own_config_path
    return plugin, manifest

def merge_config_deep(base: dict, over: dict) -> dict:

    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = merge_config_deep(out[k], v)
        else:
            out[k] = v
    return out

def build_outbound_request(params: dict, action: str) -> dict:

    is_group = action == "send_group_msg"
    target = params.get("group_id" if is_group else "user_id", 0)
    contact = contact_map.get(target, str(target))
    segs = params.get("message", [])
    text = "".join(
        s.get("data", {}).get("text", "")
        for s in segs if isinstance(s, dict) and s.get("type") == "text"
    )
    return {"contact": contact, "group": is_group, "target": target,
            "text": text, "segments": segs}

def _tick_loop(bus) -> None:

    while True:
        time.sleep(TICK_INTERVAL)
        if not runtime.running:
            continue
        try:
            bus.emit(bus.TICK, None)
        except Exception:
            pass

class FanoutPusher:

    def __init__(self, relay: "InboundRelay"):
        self.relay = relay

    def push(self, event: dict) -> bool:
        ok = False
        for p in self.relay.pushers:
            try:
                if p.push(event):
                    ok = True
            except Exception as exc:
                log.warning(f"扩展推送失败: {exc}")
        return ok

class Engine:

    def __init__(self, config_path: str = None):
        self.settings = load_settings(config_path)

        self._config_path = config_path or default_config_path()

        self._modernize_config_file()

        set_levels(self.settings.log_levels)
        runtime.self_id = wxid_to_int(self.settings.bot_wxid) if self.settings.bot_wxid else 10001

        self.bus = EventBus()
        self.sender = WeChatSender()
        self.outbound = OutboundHandler(self.settings, self.sender)

        self.onebot_clients = {}
        self.weflow_clients = {}

        self.relay = InboundRelay(self.settings, self.bus, self.sender)

        self.fanout = FanoutPusher(self.relay)

        self.ctx = Context(self.bus, self.settings, self.sender, self.fanout, log)
        self.extensions = load_extensions(self.settings, self.ctx)

        self._weflow_started = False
        self._tick_started = False

    def _modernize_config_file(self) -> None:

        try:
            if modernize(self.settings.data):
                self._write_yaml(self.settings.data)
                log.info("配置文件已整理为分组写法（如 dashboard.username），内容未变")
        except Exception as exc:
            log.warning(f"整理配置文件失败（不影响运行）: {exc}")

    def make_outbound_dispatcher(self, client: OneBotClient):

        async def on_api(data: dict) -> None:
            action = data.get("action", "")
            params = data.get("params", {})
            echo = data.get("echo", "")
            resp = {"status": "ok", "retcode": 0, "data": build_api_response(action)}
            if echo:
                resp["echo"] = echo
            await client._send_resp(resp)
            if action not in SEND_ACTIONS:
                return
            req = build_outbound_request(params, action)
            handled = False
            outbound_subs = sorted(
                self.bus._subs.get(self.bus.OUTBOUND, []), key=lambda x: x[1])
            for cb, _ in outbound_subs:
                try:
                    if self.bus.invoke(cb, req) is True:
                        handled = True
                except Exception as exc:
                    inst = getattr(cb, "__self__", None)
                    if inst is not None and hasattr(inst, "on_error"):
                        try:
                            inst.on_error("outbound", exc)
                        except Exception:
                            pass
                    log.warning(f"OUTBOUND 钩子异常: {exc}")
            if handled:
                return
            await self.outbound.send_only(action, params)
        return on_api

    def start(self) -> None:

        with runtime.run_lock:
            runtime.running = True
            runtime.paused.clear()

            try:
                from net.file_server import start_file_server
                start_file_server(self.settings.file_server_port)
            except Exception as exc:
                log.warning(f"文件服务启动失败: {exc}")
            if not self._tick_started:
                threading.Thread(
                    target=_tick_loop, args=(self.bus,), daemon=True, name="tick").start()
                self._tick_started = True
            self._sync_weflow()
            self._sync_onebot()
        log.info("Miloto 已启动")

    def stop(self) -> None:

        with runtime.run_lock:
            runtime.running = False
            for c in self.onebot_clients.values():
                c.stop()
        log.info("Miloto 已暂停")

    def pause(self) -> None:

        runtime.paused.set()
        log.info("已暂停处理（连接保持）")

    def resume(self) -> None:

        runtime.paused.clear()
        log.info("已恢复处理")

    def status(self) -> dict:

        ob_list = [
            {
                "name": c.get("name"),
                "url": c.get("url", ""),
                "token": c.get("token", ""),
                "enable": bool(c.get("enable", True)),
                "heartbeat": int(c.get("heartbeat", 20)),
                "reconnect": int(c.get("reconnect", 5)),
                "connected": (self.onebot_clients.get(c.get("name")).is_alive()
                              if self.onebot_clients.get(c.get("name")) else False),
            }
            for c in self.settings.onebot_connections
        ]
        weflow_list = [
            {
                "name": c.get("name"),
                "url": c.get("url", ""),
                "token": c.get("token", ""),
                "enable": bool(c.get("enable", True)),
                "reconnect": int(c.get("reconnect", 10)),
                "connected": (self.weflow_clients.get(c.get("name")).is_connected()
                              if self.weflow_clients.get(c.get("name")) else False),
            }
            for c in self.settings.weflow_connections
        ]
        return {
            "running": runtime.running,
            "paused": runtime.paused.is_set(),
            "onebot_connections": ob_list,
            "weflow_connections": weflow_list,
            "enabled_plugins": self.settings.enabled_plugins,
            "plugins": self.list_plugins(),
        }

    def _sync_onebot(self) -> None:

        wanted = {c.get("name"): c for c in self.settings.onebot_connections}

        for name in list(self.onebot_clients):
            if name not in wanted:
                self.onebot_clients[name].stop()
                del self.onebot_clients[name]

        for name, conf in wanted.items():
            url = conf.get("url", "")
            token = conf.get("token", "")
            heartbeat = int(conf.get("heartbeat", 20))
            reconnect = int(conf.get("reconnect", 5))

            client = self.onebot_clients.get(name)
            if client and (client.url != url or client.token != token
                           or client.heartbeat != heartbeat
                           or client.reconnect != reconnect):
                client.stop()
                client = None
            if client is None:
                client = OneBotClient(name, url, token, runtime.self_id, None,
                                      heartbeat=heartbeat, reconnect=reconnect)
                client.on_api = self.make_outbound_dispatcher(client)
                self.onebot_clients[name] = client

            if conf.get("enable", True) and runtime.running:
                client.start()
            else:
                client.stop()

        self.relay.pushers = [c for c in self.onebot_clients.values() if c._active]

    def _sync_weflow(self) -> None:

        wanted = {c.get("name"): c for c in self.settings.weflow_connections}
        storage_dir = self.settings.file_storage_dir or ""

        for name in list(self.weflow_clients):
            if name not in wanted:
                self.weflow_clients[name].stop()
                del self.weflow_clients[name]

        for name, conf in wanted.items():
            url = conf.get("url", "")
            token = conf.get("token", "")
            reconnect = int(conf.get("reconnect", 10))
            enabled = bool(conf.get("enable", True))

            client = self.weflow_clients.get(name)
            if client and (client.base != url.rstrip("/") or client.token != token
                           or client.reconnect != reconnect
                           or client.file_storage_dir != storage_dir):
                client.stop()
                client = None

            if client is None:
                client = WeFlowClient(name, url, token, self.settings.attachments,
                                      file_storage_dir=storage_dir,
                                      reconnect=reconnect)
                self.weflow_clients[name] = client
                if enabled and runtime.running:
                    self._start_weflow(client)
            elif enabled and runtime.running and not (client._thread and client._thread.is_alive()):
                self._start_weflow(client)
            elif not enabled and client._active:
                client.stop()

        self.relay.weflow_clients = list(self.weflow_clients.values())

    def _start_weflow(self, client: WeFlowClient) -> None:

        client._active = True
        t = threading.Thread(
            target=client.listen,
            args=(self.relay.on_raw,),
            daemon=True, name=f"weflow-{client.name}")
        client._thread = t
        t.start()

    def get_config_dict(self) -> dict:

        return dict(self.settings.data)

    def set_log_level(self, level: str, on: bool) -> None:

        lv = str(level).lower()
        if lv not in ("debug", "info", "warning", "error"):
            return
        on = bool(on)

        raw = self.settings.data
        ll = dict(raw.get("log_levels", {}) or {})
        ll[lv] = on
        raw["log_levels"] = ll

        self._write_yaml(raw)

        set_level_enabled(lv, on)

    def apply_config(self, data: dict) -> str:

        merged = merge_config_deep(self.settings.data, expand_paths(data))

        port = read_path(merged, "web.port")
        try:
            port = int(port)
        except (TypeError, ValueError):
            port = 0
        if not (1 <= port <= 65535):
            port = 8127
        write_path(merged, "web.port", port)
        self._write_yaml(merged)

        old_config = dict(self.settings.data)

        self.settings.data.clear()
        self.settings.data.update(merged)

        set_levels(self.settings.log_levels)

        def enabled_plugin_names(config):
            names = set()
            for name, plugin_config in (config.get("plugins", {}) or {}).items():
                disabled = (isinstance(plugin_config, dict)
                            and plugin_config.get("enabled", True) is False)
                if not disabled:
                    names.add(name)
            return names

        if enabled_plugin_names(old_config) == enabled_plugin_names(merged):
            new_plugins = merged.get("plugins", {}) or {}
            for name, plugin in self.extensions.items():
                plugin_config = new_plugins.get(name, {})
                try:
                    if plugin.ctx is not None:
                        plugin.ctx.config = plugin_config
                    plugin.on_config_change(plugin_config)
                except Exception as exc:
                    log.warning(f"插件 on_config_change 失败: {name} -> {exc}")
        else:
            self.reload_extensions()

        self._sync_onebot()
        self._sync_weflow()
        return "配置已保存并生效"

    @staticmethod
    def _connection_path(kind: str) -> str:

        return "connections.onebot" if kind == "ob" else "connections.weflow"

    def _read_connections(self, kind: str) -> list:
        found = read_path(self.settings.data, self._connection_path(kind), [])
        return list(found) if isinstance(found, list) else []

    def _save_connections(self, kind: str, connections: list) -> None:
        write_path(self.settings.data, self._connection_path(kind), connections)
        self._write_yaml(self.settings.data)

    def upsert_connection(self, kind: str, data: dict) -> tuple:

        name = (data.get("name") or "").strip()
        if not name:
            return False, "名称不能为空"
        connections = self._read_connections(kind)
        existing_index = None
        for index, item in enumerate(connections):
            if item.get("name") == name:
                existing_index = index
                break
        entry = {
            "name": name,
            "url": data.get("url", ""),
            "token": data.get("token", ""),
        }
        if kind == "ob":
            entry["heartbeat"] = max(3, min(600, int(data.get("heartbeat", 20))))
            entry["reconnect"] = max(1, min(600, int(data.get("reconnect", 5))))
        else:
            entry["reconnect"] = max(3, min(600, int(data.get("reconnect", 10))))

        if existing_index is None:

            entry["enable"] = True
            connections.append(entry)
            if kind == "ob":
                timing = f" 心跳={entry['heartbeat']}s 重连={entry['reconnect']}s"
            else:
                timing = f" 重连={entry['reconnect']}s"
            log.info(f"创建连接: [{kind}] 名称={name} 地址={entry['url']}{timing}")
        else:

            previous = connections[existing_index]
            entry["enable"] = data.get("enable", previous.get("enable", True))
            changes = []
            if previous.get("url") != entry["url"]:
                changes.append(f"地址 {previous.get('url','') or '(空)'} → {entry['url'] or '(空)'}")
            if previous.get("token") != entry["token"]:
                changes.append("Token")
            if kind == "ob" and int(previous.get("heartbeat", 20)) != entry["heartbeat"]:
                changes.append(f"心跳 {previous.get('heartbeat', 20)}s → {entry['heartbeat']}s")
            default_reconnect = 5 if kind == "ob" else 10
            if int(previous.get("reconnect", default_reconnect)) != entry["reconnect"]:
                changes.append(f"重连 {previous.get('reconnect', default_reconnect)}s → {entry['reconnect']}s")
            log.info(f"修改连接: [{kind}] 名称={name}"
                     + (" " + "，".join(changes) if changes else "（无字段变更）"))
            connections[existing_index] = entry

        self._save_connections(kind, connections)
        self._sync_connections(kind)
        return True, "已保存"

    def delete_connection(self, kind: str, name: str) -> bool:

        remaining = [item for item in self._read_connections(kind)
                     if item.get("name") != name]
        self._save_connections(kind, remaining)
        log.info(f"删除连接: [{kind}] 名称={name}")
        self._sync_connections(kind)
        return True

    def set_connection_enabled(self, kind: str, name: str, enable: bool) -> bool:

        connections = self._read_connections(kind)
        for item in connections:
            if item.get("name") == name:
                item["enable"] = enable
                log.info(f"切换连接开关: [{kind}] 名称={name} → {'启用' if enable else '停用'}")
                break
        else:
            return False
        self._save_connections(kind, connections)
        self._sync_connections(kind)
        return True

    def _sync_connections(self, kind: str) -> None:

        if kind == "ob":
            self._sync_onebot()
        else:
            self._sync_weflow()

    def list_plugins(self) -> list:

        pdir = plugins_directory()
        names = []
        if os.path.isdir(pdir):
            for entry in sorted(os.listdir(pdir)):
                full = os.path.join(pdir, entry)
                if os.path.isdir(full):
                    if os.path.exists(os.path.join(full, "__init__.py")):
                        names.append(entry)
                elif entry.endswith(".py") and not entry.startswith("_"):
                    names.append(entry[:-3])
        out = []
        for pname in sorted(names):
            meta = self._plugin_meta(pname)
            pcfg = (self.settings.plugins or {}).get(pname, {})
            enabled = not (isinstance(pcfg, dict) and pcfg.get("enabled", True) is False)
            out.append({
                "name": pname,
                "display_name": meta.get("name") or pname,
                "version": meta.get("version", ""),
                "author": meta.get("author", ""),
                "desc": meta.get("desc") or meta.get("description", ""),
                "enabled": enabled,
                "loaded": pname in self.extensions,
                "schema": self._plugin_schema(pname),
                "config": self._plugin_editable_config(pname),
            })
        return out

    def _plugin_meta(self, pname: str) -> dict:

        inst = self.extensions.get(pname)
        if inst is not None and getattr(inst, "_manifest", None):
            return inst._manifest
        pdir = plugins_directory()
        plugin_dir = os.path.join(pdir, pname) if os.path.isdir(os.path.join(pdir, pname)) else pdir
        manifest_path = os.path.join(plugin_dir, "manifest.yaml")
        if os.path.exists(manifest_path):
            try:
                with open(manifest_path, "r", encoding="utf-8") as fh:
                    return yaml.safe_load(fh) or {}
            except Exception:
                return {}
        return {}

    def _plugin_schema(self, pname: str) -> list:

        pdir = plugins_directory()
        plugin_dir = os.path.join(pdir, pname) if os.path.isdir(os.path.join(pdir, pname)) else pdir
        schema_path = os.path.join(plugin_dir, "config_schema.json")
        if os.path.exists(schema_path):
            try:
                with open(schema_path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                return data if isinstance(data, list) else data.get("fields", [])
            except Exception:
                return []
        return []

    def _plugin_editable_config(self, pname: str) -> dict:

        pcfg = (self.settings.plugins or {}).get(pname, {})
        if not isinstance(pcfg, dict):
            return {}
        return {k: v for k, v in pcfg.items() if k != "enabled"}

    def get_plugin_config(self, name: str) -> dict:

        return {"name": name, "schema": self._plugin_schema(name),
                "config": self._plugin_editable_config(name)}

    def save_plugin_config(self, name: str, cfg: dict) -> str:

        return self.apply_config({"plugins": {name: cfg}})

    def set_plugin_enabled(self, name: str, enabled: bool) -> str:

        return self.apply_config({"plugins": {name: {"enabled": bool(enabled)}}})

    def reload_plugin(self, name: str) -> bool:

        plugins_cfg = self.settings.plugins or {}
        pcfg = plugins_cfg.get(name, {}) or {}

        inst = self.extensions.get(name)
        if inst is not None:
            try:
                inst.on_unload()
            except Exception as exc:
                log.warning(f"插件 on_unload 异常 ({name}): {exc}")
            self.bus.unsubscribe(inst)
            del self.extensions[name]

        if isinstance(pcfg, dict) and pcfg.get("enabled", True) is False:
            log.info(f"[插件] {name} 已禁用，重载后保持不加载")
            return True

        pdir = plugins_directory()
        try:
            new_inst, _manifest = load_plugin_module(name, pdir, self.ctx, pcfg)
        except Exception as exc:
            log.error(f"插件重载异常: {name} -> {exc}")
            return False
        if new_inst is None:
            log.error(f"插件重载失败: {name}（模块未返回实例）")
            return False

        pctx = build_plugin_context(self.ctx, name, pcfg)
        new_inst.ctx = pctx
        new_inst.log = pctx.log
        try:
            new_inst.on_load(pctx)
        except Exception as exc:
            log.error(f"插件 on_load 失败 ({name}): {exc}")
        wire_hooks(self.ctx.bus, new_inst)
        try:
            new_inst.on_all_loaded()
        except Exception as exc:
            log.warning(f"插件 on_all_loaded 异常 ({name}): {exc}")
        self.extensions[name] = new_inst
        log.info(f"[插件] {name} 已重载")
        return True

    def install_plugin(self, name: str, source: dict) -> str:

        if not name or not isinstance(source, dict):
            return "缺少插件名或来源"
        zip_url = source.get("zip_url") or ""
        if not zip_url:
            return "该插件未提供 zip_url，无法自动安装（可手动把仓库放进 plugins/）"
        import io, zipfile, shutil, urllib.request
        pdir = plugins_directory()
        dest = os.path.join(pdir, name)

        try:
            req = urllib.request.Request(zip_url, headers={"User-Agent": "Miloto"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
        except Exception as exc:
            return f"下载失败: {exc}"

        try:
            zf = zipfile.ZipFile(io.BytesIO(data))
            names = zf.namelist()
            subdir = (source.get("subdir") or "").strip("/")
            if subdir:

                base = next((n for n in names
                             if n.rstrip("/") == subdir or n.startswith(subdir + "/")), None)
                if base is None:
                    return f"压缩包内找不到 {subdir}"
                if not base.endswith("/"):
                    base += "/"
            else:

                top = os.path.commonprefix(names)
                base = top if (top and top.endswith("/")) else (os.path.dirname(top) + "/")
            if os.path.isdir(dest):
                shutil.rmtree(dest)
            os.makedirs(dest, exist_ok=True)
            written = 0
            for n in names:
                if base and not n.startswith(base):
                    continue
                rel = n[len(base):]
                if not rel:
                    continue
                if n.endswith("/"):
                    os.makedirs(os.path.join(dest, rel), exist_ok=True)
                else:
                    target = os.path.join(dest, rel)
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    with zf.open(n) as src, open(target, "wb") as out:
                        out.write(src.read())
                    written += 1
            if written == 0:
                return "压缩包内没有可提取的文件"
        except Exception as exc:
            return f"解压失败: {exc}"

        self.reload_plugin(name)
        log.info(f"[插件市场] 已安装 {name}")
        return ""

    def uninstall_plugin(self, name: str) -> str:

        import shutil
        pdir = plugins_directory()
        folder = os.path.join(pdir, name)
        single = os.path.join(pdir, name + ".py")
        if os.path.isdir(folder):
            shutil.rmtree(folder)
        elif os.path.isfile(single):
            os.remove(single)
        else:
            return f"未找到插件 {name}"
        self.reload_extensions()
        log.info(f"[插件市场] 已卸载 {name}")
        return ""

    def reload_extensions(self) -> None:

        old_names = set(self.extensions.keys())
        for name, inst in self.extensions.items():
            try:
                inst.on_unload()
            except Exception as exc:
                log.warning(f"扩展 on_unload 异常: {name} -> {exc}")
        self.bus.clear()
        self.extensions = load_extensions(self.settings, self.ctx)

        for name in sorted(old_names - set(self.extensions.keys())):
            log.info(f"[插件] {name} 已停止（已禁用）")

    def _write_yaml(self, data: dict) -> None:

        with open(self._config_path, "w", encoding="utf-8") as fh:
            yaml.safe_dump(data, fh, allow_unicode=True, sort_keys=False)
