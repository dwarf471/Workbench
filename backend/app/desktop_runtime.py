"""Own the listener and single-instance rendezvous for one desktop session."""
import json
from pathlib import Path
import secrets
import socket
import threading
import time

import httpx
import uvicorn


class LocalService:
    def __init__(self, app, activate):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.bind(('127.0.0.1', 0))
        self.origin = f'http://127.0.0.1:{self.socket.getsockname()[1]}'
        self.token = secrets.token_urlsafe(32)
        self.app = app
        app.state.desktop_token = self.token
        app.state.desktop_origin = self.origin
        app.state.desktop_activate = activate
        config = uvicorn.Config(app, access_log=False, log_config=None, log_level='warning',
                                timeout_graceful_shutdown=5, ws='none')
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, kwargs={'sockets': [self.socket]}, daemon=True)

    def start(self, cancelled=None):
        self.thread.start()
        deadline = time.monotonic() + 60
        while not self.server.started:
            if not self.thread.is_alive():
                raise RuntimeError('本地服务启动失败，请检查数据目录及数据库版本。')
            if (cancelled and cancelled.is_set()) or time.monotonic() >= deadline:
                self.stop()
                raise RuntimeError('启动已取消或超时。')
            time.sleep(0.05)

    def stop(self):
        self.server.should_exit = True
        if self.thread.is_alive():
            self.thread.join(timeout=15)
        self.socket.close()
        if self.thread.is_alive():
            raise RuntimeError('后台服务未能及时退出；未完成任务将在下次启动时标为中断。')

    def publish(self, path: Path):
        # Session credential, not a model/MCP credential; removed at process exit.
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps({'origin': self.origin, 'token': self.token}), encoding='utf-8')
        temporary.chmod(0o600)
        temporary.replace(path)


def activate_existing(path: Path, timeout=65):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            session = json.loads(path.read_text(encoding='utf-8'))
            from urllib.parse import urlsplit
            url = urlsplit(session['origin'])
            if url.scheme != 'http' or url.hostname != '127.0.0.1' or not url.port or url.path:
                return False
            with httpx.Client(timeout=2, trust_env=False) as client:
                result = client.post(session['origin'] + '/_desktop/activate',
                                     headers={'Authorization': 'Bearer ' + session['token']})
            if result.status_code == 200:
                return True
        except (OSError, ValueError, KeyError, httpx.HTTPError):
            pass
        time.sleep(0.2)
    return False
