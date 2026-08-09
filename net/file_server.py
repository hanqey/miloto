

import os
import time
import uuid
import threading
import mimetypes
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote
from net.portutil import ensure_port_free

_SHARE = {}
_LOCK = threading.Lock()
_PORT = 8777
_SERVER = None
_STARTED = False

log = logging.getLogger("miloto.fileserver")

def start_file_server(port: int = 8777) -> None:
    """启动（幂等）本机文件服务，监听 127.0.0.1:<port>。"""
    global _PORT, _SERVER, _STARTED
    if _STARTED:
        return
    _PORT = int(port) or 8777

    ensure_port_free("127.0.0.1", _PORT, "文件服务")
    srv = ThreadingHTTPServer(("127.0.0.1", _PORT), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True,
                         name="miloto-fileserver")
    t.start()
    _SERVER = srv
    _STARTED = True
    threading.Thread(target=_cleanup_loop, daemon=True,
                     name="miloto-fs-cleanup").start()
    log.info(f"文件服务已启动: http://127.0.0.1:{_PORT}/f/<token>")

def share_file(path: str, name: str = None, ttl: int = 600) -> str | None:
    """把一个本地文件注册成 http URL。失败返回 None。"""
    if not os.path.isfile(path):
        return None
    if not _STARTED:
        return None
    token = uuid.uuid4().hex
    with _LOCK:
        _SHARE[token] = (os.path.abspath(path), time.time() + ttl,
                         name or os.path.basename(path))
    return f"http://127.0.0.1:{_PORT}/f/{token}"

def _cleanup_loop():
    while True:
        time.sleep(60)
        now = time.time()
        with _LOCK:
            expired = [k for k, v in _SHARE.items() if v[1] < now]
            for k in expired:
                _SHARE.pop(k, None)

class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if not self.path.startswith("/f/"):
            self.send_error(404)
            return
        token = self.path[len("/f/"):].split("?")[0].split("/")[0]
        with _LOCK:
            entry = _SHARE.get(token)
        if not entry:
            self.send_error(404, "not found")
            return
        abspath, expire, filename = entry
        if expire < time.time() or not os.path.isfile(abspath):
            with _LOCK:
                _SHARE.pop(token, None)
            self.send_error(410, "gone")
            return
        try:
            size = os.path.getsize(abspath)
            ctype = mimetypes.guess_type(abspath)[0] or "application/octet-stream"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(size))

            self.send_header(
                "Content-Disposition",
                f"attachment; filename*=UTF-8''{quote(filename)}"
            )
            self.end_headers()
            with open(abspath, "rb") as f:
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        except Exception:
            try:
                self.send_error(500)
            except Exception:
                pass

    def log_message(self, *args):
        pass
