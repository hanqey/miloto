

import sys
import time

from core.engine import Engine
from core.loggingx import build_logger
from core import runtime

log = build_logger("miloto.main")

ENGINE = None

def _open_browser(url: str) -> None:

    try:
        import webbrowser
        webbrowser.open(url)
    except Exception as exc:
        log.warning(f"自动打开浏览器失败（可手动访问 {url}）: {exc}")

def main() -> None:
    global ENGINE
    ENGINE = Engine()

    if "--no-gui" in sys.argv:

        ENGINE.start()
        log.info("无界面模式已启动（--no-gui）")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            ENGINE.stop()
            log.info("正在退出")
        return

    from web_panel import start_web

    ENGINE.start()
    start_web(ENGINE)
    _open_browser(f"http://127.0.0.1:{ENGINE.settings.web_port}/")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        ENGINE.stop()
        log.info("正在退出")

if __name__ == "__main__":
    main()
