"""Desktop entry point. Configure storage before importing application modules."""
import argparse
import html
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from urllib.parse import urlsplit

from filelock import FileLock, Timeout


class DesktopBridge:
    def __init__(self, data_dir):
        self._window = None
        self._service = None
        self._data_dir = data_dir
        self._saving = threading.Lock()

    def save_file(self, url, filename):
        """Only downloads from the three existing export routes are allowed."""
        import re
        import httpx
        import webview
        parsed = urlsplit(url)
        allowed = parsed.path in ('/api/archive/export', '/api/extraction/requirements/file') or re.fullmatch(
            r'/api/archive/backups/workbench-\d{8}-\d{6}-[0-9a-f]{32}\.sqlite3', parsed.path)
        if parsed.scheme or parsed.netloc or parsed.fragment or not allowed or not self._service:
            return {'error': '不支持保存此文件。'}
        if not self._saving.acquire(blocking=False):
            return {'error': '请先完成当前文件保存。'}
        temporary = None
        try:
            name = Path(str(filename)).name
            if not name or any(char in name for char in '/\\:'):
                return {'error': '文件名无效。'}
            chosen = self._window.create_file_dialog(webview.FileDialog.SAVE, save_filename=name)
            if not chosen:
                return {'saved': False}
            destination = Path(chosen if isinstance(chosen, str) else chosen[0]).resolve()
            from app.paths import ROOT
            if destination.is_relative_to(self._data_dir) or destination.is_relative_to(ROOT):
                return {'error': '请选择程序及工作台数据目录之外的位置保存。'}
            with httpx.Client(timeout=120, trust_env=False) as client:
                with client.stream('GET', self._service.origin + url,
                                   headers={'Authorization': 'Bearer ' + self._service.token}) as response:
                    if response.status_code != 200:
                        return {'error': '导出失败，请刷新后重试。'}
                    with tempfile.NamedTemporaryFile(dir=destination.parent, prefix='.workbench-', delete=False) as output:
                        temporary = Path(output.name)
                        for chunk in response.iter_bytes():
                            output.write(chunk)
                    temporary.replace(destination)
                    temporary = None
            return {'saved': True}
        except Exception:
            return {'error': '保存失败，请检查目标目录权限、文件占用及磁盘空间。'}
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)
            self._saving.release()


def splash(message):
    return ('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>我的工作台</title>'
            '<body style="font-family:system-ui;padding:64px;color:#37352f;background:#fafaf9">'
            '<h1>我的工作台</h1><p>' + html.escape(message) + '</p></body></html>')


def run(args):
    if args.smoke_test and not args.data_dir:
        raise ValueError('Smoke tests require an isolated --data-dir')
    from platformdirs import user_data_path
    data_dir = Path(args.data_dir or user_data_path('PersonalWorkbench', appauthor=False, roaming=False)).expanduser().resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    os.environ['WORKBENCH_DATA_DIR'] = str(data_dir)
    instance = data_dir / '.desktop-session.json'
    lock = FileLock(data_dir / '.desktop.lock')
    try:
        lock.acquire(timeout=0)
    except Timeout:
        from app.desktop_runtime import activate_existing
        if not activate_existing(instance):
            raise RuntimeError('工作台正在启动或退出。请完成已有窗口中的操作后重试。')
        return 0
    instance.unlink(missing_ok=True)
    service = None
    window = None
    cancelled = threading.Event()
    initialized = threading.Event()
    startup_started = threading.Event()
    startup_done = threading.Event()
    result = {'ok': False}
    try:
        import webview
        from app.paths import ROOT
        if not (ROOT / 'frontend' / 'dist' / 'index.html').is_file():
            raise RuntimeError('未找到界面资源，请先构建前端或重新安装工作台。')
        webview.settings['ALLOW_DOWNLOADS'] = False
        webview.settings['ALLOW_FILE_URLS'] = False
        bridge = DesktopBridge(data_dir)
        window = webview.create_window('我的工作台', html=splash('正在准备本地工作空间…'),
                                      js_api=bridge, width=1280, height=860, min_size=(800, 600),
                                      hidden=bool(args.smoke_test), text_select=True)
        bridge._window = window

        def activate():
            if not args.smoke_test:
                window.restore()
                window.show()

        def closing():
            if args.smoke_test:
                cancelled.set()
                return True
            if bridge._saving.locked():
                window.create_confirmation_dialog('正在保存', '请先完成或取消文件保存，再退出工作台。')
                return False
            if initialized.is_set():
                from app.sync import has_active
                from app.models import ExtractionTask
                from app.db import engine
                from sqlalchemy import select
                from sqlalchemy.orm import Session
                with Session(engine) as session:
                    active = has_active() or session.scalar(select(ExtractionTask.id).where(
                        ExtractionTask.status.in_(['queued', 'running'])).limit(1)) is not None
                if active and not window.create_confirmation_dialog('退出工作台',
                        '同步或需求提取仍在进行。退出会中断任务，已保存的数据保留；重新打开后不会自动继续。确定退出？'):
                    return False
            cancelled.set()
            return True

        window.events.closing += closing

        def start():
            nonlocal service
            startup_started.set()
            try:
                if not (data_dir / 'workbench.sqlite3').exists():
                    from app.desktop_storage import import_workspace
                    source = args.import_from
                    if not source and not args.smoke_test:
                        if window.create_confirmation_dialog('首次使用',
                                '是否导入旧工作台数据？选择“确定”后选择旧工作台目录；选择“取消”创建空白工作台。\n请先停止旧工作台。原文件会保留，跨机器的模型密钥需重新填写。'):
                            selected = window.create_file_dialog(webview.FileDialog.FOLDER)
                            if not selected:
                                window.load_html(splash('已取消导入。关闭窗口后重新启动即可再次选择。'))
                                return
                            source = selected[0]
                    if source:
                        import_workspace(Path(source), data_dir)
                elif args.import_from:
                    raise ValueError('桌面已有数据，不能覆盖导入。')
                if cancelled.is_set():
                    return
                from app.main import app
                from app.desktop_runtime import LocalService
                if cancelled.is_set():
                    return
                service = LocalService(app, activate)
                bridge._service = service
                service.start(cancelled)
                if cancelled.is_set():
                    return
                service.publish(instance)
                initialized.set()
                window.load_url(service.origin + '/_desktop/bootstrap?token=' + service.token)
                if args.smoke_test:
                    deadline = time.monotonic() + 30
                    while time.monotonic() < deadline:
                        if window.get_current_url() == service.origin + '/':
                            value = window.evaluate_js("({title: document.querySelector('h1')?.textContent, ready: document.body.innerText.includes('SQLite 已就绪'), bridge: typeof window.pywebview?.api?.save_file})")
                            if value and value.get('ready') and value.get('bridge') == 'function':
                                result.update(ok=True, ui=value, origin=service.origin,
                                              data_dir=str(data_dir), renderer='edgechromium' if sys.platform == 'win32' else 'cocoa')
                                break
                        time.sleep(0.2)
                    if not result['ok']:
                        raise RuntimeError('桌面界面就绪检查失败。')
                    import subprocess
                    command = [sys.executable]
                    if not getattr(sys, 'frozen', False):
                        command.append(str(Path(__file__).resolve()))
                    command += ['--data-dir', str(data_dir), '--smoke-test', str(args.smoke_test) + '.second']
                    duplicate = subprocess.run(command, timeout=10, capture_output=True,
                                               creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
                    result['second_launch_exit'] = duplicate.returncode
                    if duplicate.returncode:
                        result['ok'] = False
                        raise RuntimeError('重复启动检查失败。')
                    window.destroy()
            except Exception as error:
                # Log only the class; exception text may contain imported config values.
                result['ok'] = False
                result['error_type'] = type(error).__name__
                message = str(error) if isinstance(error, (ValueError, RuntimeError)) else '启动失败，请检查数据目录、磁盘空间和安装完整性。'
                if args.smoke_test:
                    window.destroy()
                elif not cancelled.is_set():
                    window.load_html(splash(message))
            finally:
                startup_done.set()

        webview.start(start, gui='edgechromium' if sys.platform == 'win32' else None,
                      private_mode=True, storage_path=str(data_dir / 'webview'),
                      localization={'global.ok': '确定', 'global.cancel': '取消',
                                    'global.quitConfirmation': '确定退出工作台？'})
    finally:
        cancelled.set()
        try:
            if startup_started.is_set():
                startup_done.wait(timeout=15)
            if service:
                service.stop()
        finally:
            instance.unlink(missing_ok=True)
            lock.release()
            if args.smoke_test:
                Path(args.smoke_test).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if not args.smoke_test or result['ok'] else 1


def main():
    parser = argparse.ArgumentParser(description='我的工作台桌面版')
    parser.add_argument('--data-dir', help='独立数据目录（开发和验收使用）')
    parser.add_argument('--import-from', help='首次启动时导入的旧工作台目录')
    parser.add_argument('--smoke-test', help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        return run(args)
    except Exception as error:
        if args.smoke_test:
            Path(args.smoke_test).write_text(json.dumps({'ok': False, 'error_type': type(error).__name__}), encoding='utf-8')
        elif sys.platform == 'win32':
            import ctypes
            ctypes.windll.user32.MessageBoxW(None,
                '工作台未能启动。请确认安装完整、WebView2 已安装，且数据目录可写。\n错误类型：' + type(error).__name__,
                '我的工作台', 0x10)
        else:
            print('Workbench could not start: ' + type(error).__name__, file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
