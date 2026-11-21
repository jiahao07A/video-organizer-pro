"""真实Qt事件循环、监控线程与UpdateJob交接；只使用指定隔离目录。"""
import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QEvent, QObject, QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon
import core.software_update as updates
from core.software_update import UpdateState
from core.video_organizer_service import VideoOrganizerService
from gui.main_window import MainWindow


def main():
    root = Path(sys.argv[1]).resolve()
    failure = sys.argv[2] if len(sys.argv) > 2 else 'none'
    updates.TRUSTED_KEYS = json.loads((root / 'public-keys.json').read_text(encoding='utf-8'))
    app = QApplication([])
    service = VideoOrganizerService(db_path=str(root / 'db.sqlite'), results_json=str(root / 'results.json'))
    service.manual_test_environment = True
    media = root / 'media'
    media.mkdir()
    service.settings['processing']['media_watch'].update({
        'directories': [{'id': 'exit-probe', 'path': str(media), 'enabled': True}],
        'auto_analyze': False, 'ask_initial_scan': False, 'initial_scan_mode': 'all',
    })
    launches, warnings = [], []
    with patch('gui.services.update_controller.update_cache', return_value=root / 'cache'), \
         patch('core.update_runtime.is_installed', return_value=True), \
         patch('core.update_runtime.prepare_install', return_value=SimpleNamespace()), \
         patch('core.update_runtime.launch_installer', side_effect=lambda *a: launches.append(True)), \
         patch.object(QMessageBox, 'warning', side_effect=lambda *a: warnings.append(a[2])), \
         patch.object(QSystemTrayIcon, 'isSystemTrayAvailable', return_value=True):
        window = MainWindow(service)
        # 保持与真实托盘相同的事件循环语义；不替换任务门禁或监控停止。
        window._tray_available = True
        app.setQuitOnLastWindowClosed(False)
        window.show()
        monitor = window.media_monitor
        assert monitor.running and monitor._observer.is_alive()
        quit_events = []
        app.aboutToQuit.connect(lambda: quit_events.append(True))
        window.update_controller.available.emit()
        assert window._update_notice.isVisible()
        recovered = []
        persistent_retry_verified = []
        stop_repaired = []

        class RejectOnce(QObject):
            def eventFilter(self, watched, event):
                if event.type() == QEvent.Close and window._update_exiting and not recovered:
                    event.ignore()
                    return True
                return False

        rejection_filter = RejectOnce(window)
        if failure == 'close_rejected':
            window.installEventFilter(rejection_filter)
        elif failure in ('monitor_error', 'persistent_monitor_error'):
            original_stop = monitor.stop
            def stop_once(**kwargs):
                if not recovered or (failure == 'persistent_monitor_error' and not stop_repaired):
                    raise OSError('synthetic monitor stop failure')
                return original_stop(**kwargs)
            monitor.stop = stop_once

        def retry_when_failed():
            if window.update_controller.state != UpdateState.FAILED:
                QTimer.singleShot(20, retry_when_failed)
                return
            assert window.centralWidget().isEnabled()
            assert window._update_notice.isEnabled()
            assert not window._update_exiting and not window._update_preparing
            theme = service.settings['ui_preferences']['theme']
            QTest.mouseClick(window.theme_btn, Qt.LeftButton)
            assert service.settings['ui_preferences']['theme'] != theme
            recovered.append(True)
            # 走正式托盘菜单的“彻底退出”，不用替代退出函数。
            warning_count = len(warnings)
            window.tray_icon.contextMenu().actions()[-1].trigger()
            if failure == 'persistent_monitor_error':
                assert window.isVisible(), '持续停止失败后不能隐藏窗口'
                assert window.centralWidget().isEnabled()
                assert not window._allow_exit and not window._close_accepted
                assert not quit_events
                assert len(warnings) == warning_count + 1, '重试失败必须再次提示'
                theme = service.settings['ui_preferences']['theme']
                QTest.mouseClick(window.theme_btn, Qt.LeftButton)
                assert service.settings['ui_preferences']['theme'] != theme
                persistent_retry_verified.append(True)
                stop_repaired.append(True)
                window.tray_icon.contextMenu().actions()[-1].trigger()

        def begin_when_idle():
            from gui.views.settings import _window_has_active_background_work
            if _window_has_active_background_work(window):
                QTimer.singleShot(20, begin_when_idle)
                return
            assert window.update_controller.state == UpdateState.READY
            window.install_update()

        QTimer.singleShot(50, begin_when_idle)
        if failure != 'none':
            QTimer.singleShot(100, retry_when_failed)
        # 看门狗仅负责让失败子进程有界返回，不作为退出实现。
        import os
        QTimer.singleShot(8000, lambda: os._exit(23))
        app.exec()
        assert launches == [True], warnings
        assert window.update_controller.state == (UpdateState.INSTALLING if failure == 'none' else UpdateState.FAILED)
        assert quit_events == [True]
        assert not monitor._thread.is_alive()
        assert not monitor._observer.is_alive()
        assert monitor._analysis_shutdown
        assert not any(t.is_alive() for t in monitor._analysis_pool._threads)
        assert not window.isVisible()
        assert bool(warnings) == (failure != 'none')
        assert bool(recovered) == (failure != 'none')
        result = {'event_loop_exited': True, 'monitor_stopped': True, 'launch_count': len(launches),
                  'persistent_retry_kept_ui_available': bool(persistent_retry_verified),
                  'thread_names_after_shutdown': [t.name for t in threading.enumerate()]}
        (root / 'exit-result.json').write_text(json.dumps(result), encoding='utf-8')
        window.deleteLater()
        app.processEvents()


if __name__ == '__main__':
    main()
