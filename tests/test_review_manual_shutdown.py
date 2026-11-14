"""人工测试退出必须在真实 QThread 结束后才销毁窗口和会话数据。"""
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QThread, QTimer
from PySide6.QtWidgets import QWidget
from shiboken6 import isValid
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from core.manual_test_environment import wait_for_background_work
from tools.manual_gui_test import _close_test_session


class WaitingThread(QThread):
    def __init__(self, release, parent):
        super().__init__(parent)
        self.release = release
        self.started_running = threading.Event()

    def run(self):
        self.started_running.set()
        self.release.wait(3)


@pytest.mark.parametrize('initial_check_error', [False, True])
def test_shutdown_preserves_window_and_data_past_first_deadline(app, tmp_path, capsys, initial_check_error):
    window = QWidget()
    release = threading.Event()
    worker = WaitingThread(release, window)
    window.worker = worker
    cancelled = []
    window.service = SimpleNamespace(request_cancel_analysis=lambda: cancelled.append(True))
    marker = tmp_path / 'session-active.txt'
    marker.write_text('data still active', encoding='utf-8')
    observed = []
    cleaned = []
    check_calls = []

    def inspect_while_running():
        observed.append((worker.isRunning(), isValid(window), marker.exists(), bool(cleaned)))
        release.set()

    def close_environment():
        assert not worker.isRunning()
        assert observed == [(True, True, True, False)]
        marker.unlink()
        cleaned.append(True)
        return {'temp_data_removed': True, 'errors': []}

    def waiter(target, timeout):
        check_calls.append(timeout)
        if initial_check_error and len(check_calls) == 1:
            raise RuntimeError('synthetic inspection failure')
        return wait_for_background_work(target, timeout=timeout)

    environment = SimpleNamespace(close=close_environment)
    worker.start()
    assert worker.started_running.wait(1)
    QTimer.singleShot(130, inspect_while_running)
    try:
        report = _close_test_session(environment, window, app, first_timeout=0.01, waiter=waiter)
        assert report['temp_data_removed'] and cleaned == [True]
        assert cancelled == [True]
        assert len(check_calls) >= 2
        stderr = capsys.readouterr().err
        assert '保留窗口和临时数据' in stderr
        if initial_check_error:
            assert 'synthetic inspection failure' in stderr
    finally:
        release.set()
        worker.wait(2000)
        if isValid(window):
            window.close()
            window.deleteLater()
        app.processEvents()


def test_shutdown_waits_for_monitor_even_when_widget_threads_are_idle(app, capsys):
    released = threading.Event()
    started = threading.Event()
    monitor_thread = threading.Thread(target=lambda: (started.set(), released.wait(3)))
    monitor_thread.start()
    assert started.wait(1)
    calls = []
    window = QWidget()
    monitor = SimpleNamespace(analysis_running=True, _thread=monitor_thread, _observer=None,
        stop=lambda **kw: calls.append(kw))
    window.media_monitor = monitor
    window.service = SimpleNamespace(request_cancel_analysis=lambda: None)
    cleaned = []

    def finish_monitor():
        assert cleaned == [] and isValid(window)
        monitor.analysis_running = False
        released.set()

    def close_environment():
        assert not monitor_thread.is_alive()
        cleaned.append(True)
        return {'temp_data_removed': True, 'errors': []}

    QTimer.singleShot(90, finish_monitor)
    try:
        _close_test_session(SimpleNamespace(close=close_environment), window, app,
            first_timeout=0.01, waiter=lambda *a, **kw: True)
        assert calls == [{'wait': False, 'shutdown_analysis': True}]
        assert cleaned == [True]
        assert '继续等待' in capsys.readouterr().err
    finally:
        released.set()
        monitor_thread.join(2)
        if isValid(window):
            window.deleteLater()
        app.processEvents()


def test_shutdown_without_window_cleans_environment():
    calls = []
    report = {'temp_data_removed': True, 'errors': []}
    environment = SimpleNamespace(close=lambda: calls.append(True) or report)
    assert _close_test_session(environment, None, None) == report
    assert calls == [True]


def test_shutdown_keeps_qt_responsive_while_real_observer_stop_waits(app, tmp_path):
    entered, release = threading.Event(), threading.Event()

    class BlockedHandler(FileSystemEventHandler):
        def on_created(self, event):
            entered.set()
            # 即使停止代码回归，真实监听线程也必须能自行退出，避免测试挂死。
            release.wait(3)

    observer = Observer()
    observer.schedule(BlockedHandler(), str(tmp_path), recursive=False)
    observer.start()
    window = QWidget()
    marker = tmp_path / 'active.txt'
    observations, cleaned, stop_threads = [], [], []

    def stop_monitor(**kwargs):
        assert kwargs == {'wait': False, 'shutdown_analysis': True}
        stop_threads.append(threading.current_thread())
        observer.stop()

    window.media_monitor = SimpleNamespace(_thread=None, _observer=observer,
        analysis_running=False, stop=stop_monitor)

    def release_observer():
        observations.append((observer.is_alive(), isValid(window), marker.exists(), bool(cleaned)))
        release.set()

    def close_environment():
        assert observations == [(True, True, True, False)]
        assert not observer.is_alive()
        assert len(stop_threads) == 1
        assert stop_threads[0] is not threading.main_thread()
        assert not stop_threads[0].daemon and not stop_threads[0].is_alive()
        cleaned.append(True)
        return {'temp_data_removed': True, 'errors': []}

    timer = QTimer(window)
    timer.setSingleShot(True)
    timer.timeout.connect(release_observer)
    try:
        marker.write_text('synthetic media', encoding='utf-8')
        assert entered.wait(2)
        timer.start(100)
        _close_test_session(SimpleNamespace(close=close_environment), window, app,
            first_timeout=0.01, waiter=lambda *a, **kw: True)
        assert cleaned == [True]
    finally:
        release.set()
        observer.stop()
        observer.join(3)
        if isValid(window):
            timer.stop()
            window.deleteLater()
        app.processEvents()


def test_shutdown_retries_stop_errors_without_authorizing_cleanup(app, tmp_path, capsys):
    window = QWidget()
    marker = tmp_path / 'active.txt'
    marker.write_text('synthetic media', encoding='utf-8')
    allow_stop = threading.Event()
    stopped = threading.Event()
    attempts, observations, cleaned = [], [], []

    def stop_monitor(**kwargs):
        attempts.append(threading.current_thread())
        if not allow_stop.is_set():
            raise RuntimeError('synthetic stop failure')
        stopped.set()

    # 后台原本已空闲，停止异常仍不能被当成允许清理的依据。
    window.media_monitor = SimpleNamespace(_thread=None, _observer=None,
        analysis_running=False, stop=stop_monitor)

    def enable_retry():
        observations.append((len(attempts) >= 2, isValid(window), marker.exists(), bool(cleaned)))
        allow_stop.set()

    def close_environment():
        assert stopped.is_set()
        assert observations == [(True, True, True, False)]
        assert all(not thread.is_alive() and not thread.daemon for thread in attempts)
        cleaned.append(True)
        return {'temp_data_removed': True, 'errors': []}

    timer = QTimer(window)
    timer.setSingleShot(True)
    timer.timeout.connect(enable_retry)
    timer.start(350)
    try:
        _close_test_session(SimpleNamespace(close=close_environment), window, app,
            first_timeout=0.01, waiter=lambda *a, **kw: True)
        assert cleaned == [True]
        assert 'synthetic stop failure' in capsys.readouterr().err
    finally:
        allow_stop.set()
        if isValid(window):
            timer.stop()
            window.deleteLater()
        app.processEvents()


def test_offscreen_manual_main_exits_and_reports_cleanup(tmp_path):
    root = Path(__file__).resolve().parents[1]
    diagnostics = tmp_path / 'diagnostics'
    result = subprocess.run([sys.executable, '-X', 'utf8', str(root / 'tools' / 'manual_gui_test.py'),
        '--offscreen', '--records', '0', '--no-media-files', '--diagnostics-root', str(diagnostics)],
        cwd=str(root), env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen'},
        capture_output=True, text=True, encoding='utf-8', timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
    reports = list(diagnostics.glob('*/cleanup-report.json'))
    assert len(reports) == 1
    report = json.loads(reports[0].read_text(encoding='utf-8'))
    assert report['temp_data_removed'] is True and report['errors'] == []
    assert not Path(report['temp_root']).exists()
    assert 'QThread: Destroyed while thread' not in result.stderr
