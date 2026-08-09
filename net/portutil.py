

import socket
import sys
import logging

log = logging.getLogger("miloto.portutil")

def port_in_use(host: str, port: int) -> bool:

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.5)
    try:
        s.bind((host, port))
        return False
    except OSError:
        return True
    finally:
        try:
            s.close()
        except Exception:
            pass

def _find_pid(port: int):

    try:
        import subprocess
        res = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"],
            capture_output=True, timeout=4,
        )
        out = res.stdout.decode("utf-8", "ignore") if res.stdout else ""
    except Exception:
        return None
    target = f":{port}"
    for line in out.splitlines():
        cols = line.split()
        if len(cols) < 5:
            continue
        if cols[3].upper() != "LISTENING":
            continue
        local = cols[1]

        if local.endswith(target) or local.endswith(f"]{target}"):
            return cols[4]
    return None

def ensure_port_free(host: str, port: int, label: str = "服务") -> None:

    if not port_in_use(host, port):
        return
    pid = _find_pid(port)
    msg = (
        f"\n[{label}] 端口 {port} 已被占用！\n"
        f"    很可能是另一个 Miloto 进程仍在运行（旧进程会持续提供旧页面 / 旧接口，\n"
        f"    导致你改了代码、刷新浏览器也看不到效果）。\n"
        f"    请先结束占用该端口的进程，再重新启动 Miloto。\n"
    )
    if pid:
        msg += f"    占用进程 PID 疑似: {pid}\n"
        msg += f"    Windows 结束命令: taskkill /PID {pid} /F\n"
    else:
        msg += (
            f"    Windows 查找命令: netstat -ano | findstr :{port}\n"
            f"    然后: taskkill /PID <查到的PID> /F\n"
        )
    log.error(msg)
    print(msg, file=sys.stderr)
    raise SystemExit(1)
