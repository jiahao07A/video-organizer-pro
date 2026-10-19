"""数据目录切换的 GUI 保护测试。"""
from __future__ import annotations

import os
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QRunnable, QThread, QThreadPool, QTimer
from PySide6.QtWidgets import QApplication, QWidget

from gui.views.settings import _window_has_active_background_work


class _WaitingWorker(QThread):
    def __init__(self, release: threading.Event):
        super().__init__()
        self.release = release

    def run(self):
        self.release.wait(3)


class _WaitingRunnable(QRunnable):
    def __init__(self, release: threading.Event):
        super().__init__()
        self.release = release

    def run(self):
        self.release.wait(3)


def test_data_directory_switch_detects_unparented_analysis_worker():
    app = QApplication.instance() or QApplication([])
    window = QWidget()
    child = QWidget(window)
    release = threading.Event()
    worker = _WaitingWorker(release)
    child.worker = worker
    worker.start()
    try:
        assert _window_has_active_background_work(window) is True
    finally:
        release.set()
        assert worker.wait(3000)
        window.close()
        app.processEvents()


def test_data_directory_switch_detects_thumbnail_model_thread_pool():
    app = QApplication.instance() or QApplication([])
    window = QWidget()
    child = QWidget(window)
    release = threading.Event()
    model = QWidget()
    model.thumbnail_pool = QThreadPool()
    model.thumbnail_pool.start(_WaitingRunnable(release))
    child.model = model
    try:
        assert _window_has_active_background_work(window) is True
    finally:
        release.set()
        assert model.thumbnail_pool.waitForDone(3000)
        window.close()
        model.close()
        app.processEvents()


def test_startup_directory_migration_keeps_gui_event_loop_responsive():
    from video_organizer_app import _run_legacy_data_migration

    app = QApplication.instance() or QApplication([])
    timer_fired = []

    class SlowMigration:
        def migrate_legacy(self, target_root):
            time.sleep(0.15)

    QTimer.singleShot(20, lambda: timer_fired.append(True))

    assert _run_legacy_data_migration(SlowMigration(), "unused-target") is True
    assert timer_fired == [True]
    app.processEvents()
