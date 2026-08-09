

import asyncio
import json
import threading

import websockets

from core import runtime
from core.loggingx import build_logger

log = build_logger("miloto.ob11")

class OneBotClient:
    def __init__(self, name: str, url: str, token: str, self_id: int, on_api, heartbeat: int = 20, reconnect: int = 5):
        self.name = name

        raw_url = (url or "").strip()
        if raw_url.startswith("http://"):
            norm_url = "ws://" + raw_url[len("http://"):]
        elif raw_url.startswith("https://"):
            norm_url = "wss://" + raw_url[len("https://"):]
        elif raw_url.startswith("ws://") or raw_url.startswith("wss://"):
            norm_url = raw_url
        else:

            norm_url = "ws://" + raw_url if raw_url else raw_url
        if norm_url != raw_url:
            log.warning(f"[{name}] 地址协议已纠正: {raw_url} → {norm_url}")
        self.url = norm_url
        self.token = token
        self.self_id = self_id
        self.on_api = on_api

        self.heartbeat = max(3, int(heartbeat))

        self.reconnect = max(1, int(reconnect))
        self._loop = None
        self._ws = None
        self._active = True
        self._thread = None

    def start(self) -> None:

        if self._thread and self._thread.is_alive():
            return
        self._active = True
        t = threading.Thread(target=self._run, daemon=True, name=f"ob11-{self.name}")
        self._thread = t
        t.start()

    def stop(self) -> None:

        self._active = False
        if self._ws is not None and self._loop is not None:
            try:
                self._loop.call_soon_threadsafe(self._ws.close)
            except Exception:
                pass

    def is_alive(self) -> bool:

        return (self._thread is not None and self._thread.is_alive()
                and self._ws is not None)

    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._main())
        finally:
            try:
                self._loop.close()
            except Exception:
                pass

    async def _main(self) -> None:

        while self._active and runtime.running:
            ws = None
            dropped = False
            try:
                log.info(f"[{self.name}] 正在连接 AstrBot: {self.url}")
                headers = {
                    "X-Self-ID": str(self.self_id),
                    "X-Client-Role": "Universal",
                    "User-Agent": "OneBot/11",
                }
                if self.token:
                    headers["Authorization"] = f"Bearer {self.token}"

                ws_kwargs = {
                    "additional_headers": headers,
                    "max_size": 100 * 1024 * 1024,
                    "ping_interval": self.heartbeat,
                    "ping_timeout": max(5, self.heartbeat // 2),
                    "close_timeout": 5,
                }
                try:
                    import inspect
                    if "user_agent_header" in inspect.signature(websockets.connect).parameters:
                        ws_kwargs["user_agent_header"] = None
                except Exception:
                    pass

                async with websockets.connect(self.url, **ws_kwargs) as ws:
                    self._ws = ws
                    log.info(f"[{self.name}] 已连接到 AstrBot（连接开启）")
                    try:
                        async for raw in ws:

                            if not self._active or not runtime.running:
                                break
                            try:
                                data = json.loads(raw)
                                if self.on_api:
                                    await self.on_api(data)
                            except json.JSONDecodeError:
                                log.warning(f"[{self.name}] 收到无效 JSON")
                            except Exception as exc:
                                log.error(f"[{self.name}] 处理 API 异常: {exc}")
                    finally:

                        if self._ws is ws:
                            self._ws = None
            except websockets.exceptions.ConnectionClosed as exc:
                dropped = True
                log.warning(f"[{self.name}] 连接已断开 (code={exc.code})")
            except (ConnectionRefusedError, OSError) as exc:
                dropped = True
                log.warning(f"[{self.name}] 无法连接 AstrBot ({exc})")
            except Exception as exc:
                dropped = True
                log.error(f"[{self.name}] 连接异常: {exc}")
            finally:
                if self._ws is ws:
                    self._ws = None

            if not self._active or not runtime.running:
                log.info(f"[{self.name}] 连接已关闭")
            elif dropped:
                log.warning(f"[{self.name}] 连接已断开，{self.reconnect} 秒后重连")
            else:

                log.warning(f"[{self.name}] 连接已断开（远端关闭），{self.reconnect} 秒后重连")
            if not self._active or not runtime.running:
                break
            await asyncio.sleep(self.reconnect)

    async def _send_resp(self, resp: dict) -> None:

        if not self._ws:
            return
        try:
            await self._ws.send(json.dumps(resp, ensure_ascii=False))
        except Exception as exc:
            log.warning(f"[{self.name}] 回响应失败: {exc}")

    def push(self, event: dict) -> bool:

        if not self._ws or not self._loop:
            return False
        try:
            fut = asyncio.run_coroutine_threadsafe(
                self._ws.send(json.dumps(event, ensure_ascii=False)),
                self._loop,
            )
            fut.result(timeout=5)
            return True
        except Exception as exc:
            log.warning(f"[{self.name}] 推送事件失败: {exc}")
            return False
