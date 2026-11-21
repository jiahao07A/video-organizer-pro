"""停止超时不能绕过维护门禁；结束后的再次操作必须可恢复。"""
import threading
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox, QWidget

import core.media_watch as media_watch
from gui.main_window import MainWindow
from gui.views.settings import SettingsView


@pytest.fixture
def blocked_monitor(monkeypatch):
    monkeypatch.setattr(media_watch, 'SHUTDOWN_JOIN_SECONDS', 0.02)
    monitor = media_watch.MediaDirectoryMonitor(SimpleNamespace(db=object()), {'directories': []})
    entered, release = threading.Event(), threading.Event()
    worker = threading.Thread(target=lambda: (entered.set(), release.wait()), daemon=True)
    monitor._thread = worker
    worker.start()
    assert entered.wait(timeout=2)
    try:
        yield monitor, release, worker
    finally:
        release.set()
        worker.join(timeout=2)
        monitor.stop(shutdown_analysis=True)
        assert not worker.is_alive()


@pytest.mark.parametrize('operation', ['backup', 'restore'])
def test_maintenance_timeout_warns_without_data_operations_and_allows_retry(app, monkeypatch, blocked_monitor, operation):
    monitor, release, worker = blocked_monitor
    calls, warnings, quits = [], [], []

    class Maintenance(QWidget):
        _stop_monitor_for_maintenance = SettingsView._stop_monitor_for_maintenance
        _has_running_background_work = lambda self: False

    view = Maintenance()
    view.media_monitor = monitor
    view.prefs_controller = SimpleNamespace(flush_now=lambda: True, cancel=lambda: None)
    view.service = SimpleNamespace(
        backup_configuration=lambda: (calls.append('backup') or 'backup.zip'),
        list_backups=lambda: (calls.append('list') or ['backup.zip']),
        restore_configuration=lambda choice: (calls.append(('restore', choice)) or True),
    )
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: warnings.append(args[1:]))
    monkeypatch.setattr(QMessageBox, 'information', lambda *args: None)
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.Yes)
    monkeypatch.setattr(QInputDialog, 'getItem', lambda *args: ('backup.zip', True))
    monkeypatch.setattr(QApplication, 'quit', lambda *args: quits.append(True))
    action = getattr(SettingsView, f'_{operation}_configuration')
    try:
        action(view)
        assert len(warnings) == 1
        assert warnings[0][0] == ('暂时无法备份' if operation == 'backup' else '暂时无法恢复')
        assert not calls and not quits
        assert worker.is_alive()
        release.set()
        worker.join(timeout=2)
        action(view)
        assert len(warnings) == 1
        assert calls == (['backup'] if operation == 'backup' else ['list', ('restore', 'backup.zip')])
        assert quits == ([] if operation == 'backup' else [True])
    finally:
        view.deleteLater()
        app.processEvents()


def test_reconfigure_timeout_preserves_config_warns_and_allows_retry(app, monkeypatch, blocked_monitor, tmp_path):
    monitor, release, worker = blocked_monitor
    warnings = []
    view = QWidget()
    view.media_monitor = monitor
    view.settings_page = view.library_page = None
    requested = {'directories': [{'id': 'new', 'path': str(tmp_path), 'enabled': False}]}
    view.service = SimpleNamespace(settings={'processing': {'media_watch': requested}})
    original = monitor.config
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: warnings.append(args[1:]))
    try:
        MainWindow._reconfigure_media_monitor(view)
        assert len(warnings) == 1 and '稍后再次保存' in warnings[0][1]
        assert monitor.config is original
        assert worker.is_alive()
        assert view.service.settings['processing']['media_watch'] == requested
        release.set()
        worker.join(timeout=2)
        MainWindow._reconfigure_media_monitor(view)
        assert len(warnings) == 1
        assert monitor.config == media_watch.normalize_watch_config(requested)
    finally:
        view.deleteLater()
        app.processEvents()
