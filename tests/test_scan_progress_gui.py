"""扫描进度经过真实 QThread/Qt 队列交付，不控制可见桌面。"""
import threading
import time

import pytest
from PySide6.QtCore import QThread, QTimer
from PySide6.QtWidgets import QMessageBox, QWidget

from core.manual_test_environment import wait_for_background_work
from core.video_organizer_service import SettingsManager, VideoOrganizerService
from gui.views.workstation import WorkstationView
from gui.workers.io_worker import run_io_job


def pump_until(app, predicate, timeout=4):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(0.005)
    assert predicate(), "Qt 后台任务/进度没有在期限内到达"


@pytest.fixture
def view(tmp_path, monkeypatch, qt_app):
    monkeypatch.setattr('core.video_organizer_service.TAG_CONFIG_FILE', str(tmp_path / 'tags.json'))
    monkeypatch.setattr('core.video_organizer_service.get_settings_file_path', lambda: str(tmp_path / 'settings.json'))
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: None)
    service = VideoOrganizerService(
        settings=SettingsManager.deep_copy_defaults(), on_log=lambda _: None,
        db_path=str(tmp_path / 'catalog.db'), results_json=str(tmp_path / 'results.json'),
        results_csv=str(tmp_path / 'results.csv'),
    )
    widget = WorkstationView(service)
    assert wait_for_background_work(widget)
    qt_app.processEvents()
    yield widget
    assert wait_for_background_work(widget)
    qt_app.processEvents()
    for timer in widget.findChildren(QTimer):
        timer.stop()
    widget.close()
    widget.deleteLater()
    qt_app.processEvents()


@pytest.mark.parametrize('iteration', range(5))
def test_immediate_progress_reaches_main_thread_before_completion(qt_app, iteration):
    parent = QWidget()
    main_ident = threading.get_ident()
    received = []
    completed = []
    thread_ids = []

    def job():
        thread_ids.append(threading.get_ident())
        parent._io_worker.progress_snapshot.emit({'completed': iteration})
        return iteration

    def receive(snapshot):
        received.append((snapshot, threading.get_ident(), QThread.currentThread() == qt_app.thread()))

    try:
        run_io_job(parent, fn=job, on_progress=receive,
                   on_ok=lambda result: completed.append((result, len(received))))
        pump_until(qt_app, lambda: bool(completed))
        assert parent._io_worker is None
        assert thread_ids != [main_ident]
        assert received == [({'completed': iteration}, main_ident, True)]
        assert completed == [(iteration, 1)]  # 不能在结束清理时丢弃早期队列信号。
    finally:
        worker = getattr(parent, '_io_worker', None)
        if worker is not None:
            assert worker.wait(4000)
            pump_until(qt_app, lambda: parent._io_worker is None)
        parent.deleteLater()
        qt_app.processEvents()


@pytest.mark.parametrize('fails', [False, True])
def test_scan_stages_counts_elapsed_and_finish_restore_controls(view, monkeypatch, qt_app, fails):
    next_phase, finish = threading.Event(), threading.Event()
    callbacks = []
    reloads = []
    messages = []
    main_ident = threading.get_ident()
    view.status_message.connect(lambda text: messages.append((text, threading.get_ident())))
    monkeypatch.setattr(view, 'load_data', lambda: reloads.append(True))

    def scan(progress_callback=None):
        callbacks.append(threading.get_ident())
        progress_callback({
            'phase': 'discovery', 'phase_label': '枚举视频', 'completed': 42,
            'total': 0, 'percent': 0, 'elapsed_seconds': 1.2, 'files_found': 42,
        })
        assert next_phase.wait(4)
        progress_callback({
            'phase': 'fingerprint', 'phase_label': '计算完整指纹', 'completed': 1,
            'total': 2, 'percent': 350, 'elapsed_seconds': 2.4, 'files_found': 42,
            'new_count': 1, 'duplicate_count': 3, 'error_count': 1,
        })
        assert finish.wait(4)
        if fails:
            raise OSError('测试读取失败')
        progress_callback({
            'phase': 'complete', 'phase_label': '扫描完成', 'completed': 42,
            'total': 42, 'percent': 1000, 'elapsed_seconds': 3.6, 'files_found': 42,
            'new_count': 1, 'duplicate_count': 3, 'error_count': 1,
        })
        return ['new.mp4']

    monkeypatch.setattr(view.service, 'scan_and_register_work_scope', scan)
    try:
        view.background_scan_work_scope(reason='测试扫盘')
        pump_until(qt_app, lambda: '已发现 42' in view.status_label.text())
        assert callbacks != [main_ident]
        assert view.progress_bar.maximum() == 1000
        assert view.progress_bar.value() == 0
        assert not view.progress_bar.isHidden()
        assert view.sub_progress_bar.minimum() == view.sub_progress_bar.maximum() == 0
        assert not view.sub_progress_bar.isHidden()
        assert view.sub_progress_caption.text() == '阶段'
        assert '已用 1.2 秒' in view.status_label.text()
        assert not view.analyze_btn.isEnabled()
        assert reloads == []

        next_phase.set()
        pump_until(qt_app, lambda: view.progress_bar.value() == 350)
        assert view.progress_bar.format() == '扫描总 35.0%'
        assert view.sub_progress_bar.maximum() == 2
        assert view.sub_progress_bar.value() == 1
        assert view.sub_progress_bar.format() == '计算完整指纹 1/2'
        assert '新增 1 · 重复 3 · 失败 1' in view.status_label.text()
        assert '已用 2.4 秒' in view.status_label.text()

        finish.set()
        pump_until(qt_app, lambda: getattr(view, '_io_worker', None) is None)
        assert view.analyze_btn.isEnabled()
        assert view.progress_bar.isHidden() and view.sub_progress_bar.isHidden()
        assert view.progress_bar.maximum() == view.sub_progress_bar.maximum() == 100
        assert view.sub_progress_caption.text() == '本轮'
        assert all(ident == main_ident for _, ident in messages)
        if fails:
            assert '测试扫盘失败' in view.status_label.text()
            assert '测试读取失败' in view.status_label.text()
            assert reloads == []
        else:
            assert reloads == [True]
            assert '新入库未分析 1 个 · 已用 3.6 秒' in view.status_label.text()
            assert '重复 3 · 失败 1' in view.status_label.text()
            assert any('扫描完成 42/42' in text for text, _ in messages)
    finally:
        next_phase.set()
        finish.set()
        pump_until(qt_app, lambda: getattr(view, '_io_worker', None) is None)
