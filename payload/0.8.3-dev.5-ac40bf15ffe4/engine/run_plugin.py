"""Bind an OS-assigned loopback port before publishing it to the parent plugin."""
import json
import os
from pathlib import Path
import socket
import sys
import signal
import threading
import time
import uvicorn

def main():
    parent = os.getppid()
    def watch_parent():
        while os.getppid() == parent: time.sleep(1)
        os.kill(os.getpid(), signal.SIGTERM)
    threading.Thread(target=watch_parent, daemon=True).start()
    ready = Path(sys.argv[1])
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    sock.listen(128)
    fd = os.open(ready, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'w') as stream: json.dump({'port': sock.getsockname()[1]}, stream)
    try:
        uvicorn.Server(uvicorn.Config('natural_data.server:app', log_level='warning', access_log=False)).run(sockets=[sock])
    finally:
        sock.close()
        ready.unlink(missing_ok=True)

if __name__ == '__main__': main()
