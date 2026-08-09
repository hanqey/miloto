

import os
import time
import threading

from core.loggingx import build_logger

class Watcher:
    def __init__(self, directory: str, bus, log):
        self.dir = directory
        self.bus = bus
        self.log = log
        self._seen = {}
        self._stop = False

    def start(self) -> None:
        t = threading.Thread(target=self._loop, daemon=True, name="file-watcher")
        t.start()

    def stop(self) -> None:
        self._stop = True

    def _loop(self) -> None:
        while not self._stop:
            self._scan()
            time.sleep(1.0)

    def _scan(self) -> None:
        for root, _dirs, files in os.walk(self.dir):
            for fn in files:
                path = os.path.join(root, fn)
                try:
                    size = os.path.getsize(path)
                except OSError:
                    continue
                if path in self._seen and self._seen[path] == size:

                    del self._seen[path]
                    self.bus.emit(self.bus.FILE_DROPPED,
                                  (path, {"mtime": os.path.getmtime(path)}))
                    self.log.info(f"检测到落盘文件: {os.path.basename(path)}")
                else:

                    self._seen[path] = size
