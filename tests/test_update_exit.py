"""更新交接必须退出真实事件循环，不能仅隐藏主窗口。"""
import os
import json
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest
from tests.support.updates import signed  # noqa: F401 - 临时签名密钥夹具


@pytest.mark.parametrize('failure', ['none', 'close_rejected', 'monitor_error', 'persistent_monitor_error'])
def test_prepared_signal_exits_real_window_and_monitor(tmp_path, signed, failure):
    import core.software_update as updates
    raw, signature = signed.encode()
    cache = tmp_path / 'cache'
    directory = cache / signed.data['version']
    directory.mkdir(parents=True)
    (directory / signed.installer.name).write_bytes(signed.installer.read_bytes())
    (directory / 'update.json').write_bytes(raw)
    (directory / 'update.json.sig').write_bytes(signature)
    (cache / 'ready.json').write_text(json.dumps({'version': signed.data['version']}), encoding='utf-8')
    (tmp_path / 'public-keys.json').write_text(json.dumps(updates.TRUSTED_KEYS), encoding='utf-8')
    result = subprocess.run([sys.executable, '-X', 'utf8', 'tests/support/update_exit_probe.py', str(tmp_path), failure],
                            cwd=Path(__file__).resolve().parents[1],
                            env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen'},
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads((tmp_path / 'exit-result.json').read_text(encoding='utf-8'))
    assert evidence['event_loop_exited'] and evidence['monitor_stopped']
    if failure == 'persistent_monitor_error':
        assert evidence['persistent_retry_kept_ui_available']


@pytest.mark.parametrize('failure', ['close_rejected', 'monitor_error'])
def test_failed_update_exit_restores_ui_and_allows_retry(app, tmp_path, monkeypatch, failure):
    from PySide6.QtWidgets import QApplication, QMainWindow, QWidget, QMessageBox
    from types import SimpleNamespace
    from gui.main_window import MainWindow
    from gui.services.update_controller import UpdateController
    from core.software_update import UpdateState

    monkeypatch.setattr('gui.services.update_controller.update_cache', lambda: tmp_path / 'cache')
    monkeypatch.setattr('gui.views.settings._window_has_active_background_work', lambda _: False)
    monkeypatch.setattr('core.update_runtime.launch_installer', lambda *_: None)
    monkeypatch.setattr(QMessageBox, 'warning', lambda *_: None)
    quit_requests = []
    monkeypatch.setattr(QApplication, 'quit', lambda *_: quit_requests.append(True))
    window = MainWindow.__new__(MainWindow)
    QMainWindow.__init__(window)
    window.setCentralWidget(QWidget(window))
    controller = UpdateController(SimpleNamespace(manual_test_environment=True), window)
    controller.state = UpdateState.PREPARING
    controller.release = SimpleNamespace(filename='never-execute.exe', version='0.0.3')
    window.update_controller = controller
    window._update_preparing = True
    window._update_exiting = False
    window._update_notice = None
    window._tray_available = True
    window._allow_exit = False
    window._close_accepted = False
    window.settings_page = None
    window.media_monitor = SimpleNamespace(analysis_running=False, stop=lambda **kw: None)
    window.centralWidget().setEnabled(False)
    controller.changed.connect(window._update_state_changed)
    if failure == 'close_rejected':
        monkeypatch.setattr(window, 'close', lambda: False)
    else:
        def fail_stop(**kwargs):
            raise OSError('synthetic monitor stop failure')
        window.media_monitor.stop = fail_stop
    try:
        window._launch_update(SimpleNamespace())
        assert controller.state == UpdateState.FAILED
        assert window.centralWidget().isEnabled(), '退出失败不能让主界面永久禁用'
        assert not window._update_preparing and not window._update_exiting
        assert not quit_requests
        window.media_monitor.stop = lambda **kw: None
        monkeypatch.setattr(window, 'close', lambda: MainWindow.closeEvent(window, AcceptedClose()))
        window._exit_application()
        assert quit_requests == [True], '失败后彻底退出必须仍可用'
    finally:
        window.deleteLater()
        app.processEvents()


class AcceptedClose:
    def accept(self):
        pass

    def ignore(self):
        raise AssertionError('重试退出不应被拒绝')


@pytest.mark.parametrize('blocked', [False, True])
def test_update_exits_tray_event_loop_in_subprocess(tmp_path, blocked):
    script = textwrap.dedent('''
        import sys
        from pathlib import Path
        from types import SimpleNamespace
        from unittest.mock import patch
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication, QMainWindow
        from gui.main_window import MainWindow
        from core.software_update import UpdateState
        app = QApplication([])
        app.setQuitOnLastWindowClosed(False)
        launches = []
        quit_events = []
        app.aboutToQuit.connect(lambda: quit_events.append(True))
        class Probe(QMainWindow):
            _launch_update = MainWindow._launch_update
            _exit_application = MainWindow._exit_application
            _request_application_exit = MainWindow._request_application_exit
            _stop_monitor_for_exit = MainWindow._stop_monitor_for_exit
            closeEvent = MainWindow.closeEvent
            def __init__(self):
                super().__init__()
                self._update_preparing = True
                self._update_exiting = False
                self._update_notice = None
                self._tray_available = True
                self._allow_exit = False
                self._close_accepted = False
                self.settings_page = None
                self.media_monitor = SimpleNamespace(analysis_running=False, stop=lambda **kwargs: None)
                self.status_bar = SimpleNamespace(showMessage=lambda *a: None)
                self.update_controller = SimpleNamespace(
                    release_directory=lambda: Path(sys.argv[1]),
                    release=SimpleNamespace(filename='never-execute.exe', version='0.0.2'),
                    cache=Path(sys.argv[1]), state=UpdateState.PREPARING,
                    busy=False, transfer=None,
                    _fail=self.failed_exit)
                self.update_controller._set = lambda state, msg: setattr(self.update_controller, 'state', state)
                self.block_once = sys.argv[2] == 'True'
            def failed_exit(self, message):
                assert '未能退出' in message
                self.update_controller.state = UpdateState.FAILED
                self._update_exiting = False
        w = Probe()
        w.show()
        original_close = QMainWindow.close
        def close_with_one_rejection():
            if w.block_once:
                w.block_once = False
                w._close_accepted = False
                return False
            return original_close(w)
        w.close = close_with_one_rejection
        def launch():
            w._launch_update(SimpleNamespace())
            if sys.argv[2] == 'True':
                w._exit_application()
        with patch('core.update_runtime.launch_installer', side_effect=lambda *a: launches.append(True)), patch('gui.views.settings._window_has_active_background_work', return_value=False):
            QTimer.singleShot(0, launch)
            QTimer.singleShot(1500, lambda: os_exit())
            def os_exit():
                import os
                os._exit(23)
            app.exec()
        assert launches == [True]
        assert w.update_controller.state == (UpdateState.FAILED if sys.argv[2] == 'True' else UpdateState.INSTALLING)
        assert quit_events == [True]
        assert not w.isVisible()
    ''')
    result = subprocess.run([sys.executable, '-X', 'utf8', '-c', script, str(tmp_path), str(blocked)],
                            cwd=Path(__file__).resolve().parents[1],
                            env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen'},
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
