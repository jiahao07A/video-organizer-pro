"""更新交接必须退出真实事件循环，不能仅隐藏主窗口。"""
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


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
                    _fail=lambda msg: (_ for _ in ()).throw(AssertionError(msg)))
                self.update_controller._set = lambda state, msg: setattr(self.update_controller, 'state', state)
                self.block_once = sys.argv[2] == 'True'
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
        assert w.update_controller.state == UpdateState.INSTALLING
        assert quit_events == [True]
        assert not w.isVisible()
    ''')
    result = subprocess.run([sys.executable, '-X', 'utf8', '-c', script, str(tmp_path), str(blocked)],
                            cwd=Path(__file__).resolve().parents[1],
                            env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen'},
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
