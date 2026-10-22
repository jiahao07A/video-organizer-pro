"""数据目录切换的 GUI 保护测试。"""
from __future__ import annotations

import os
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QRunnable, QThread, QThreadPool, QTimer
from PySide6.QtWidgets import QApplication, QFileDialog, QWidget

from core.manual_test_environment import ManualTestEnvironment
from gui.views.settings import SettingsView
from gui.views.settings import _window_has_active_background_work
from gui.widgets.media_preview import HAS_MULTIMEDIA, MediaPreviewWidget


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


def test_data_directory_switch_detects_python_thread_on_child_widget():
    app = QApplication.instance() or QApplication([])
    window = QWidget()
    child = QWidget(window)
    release = threading.Event()
    worker = threading.Thread(target=release.wait, args=(3,), daemon=True)
    child.worker = worker
    worker.start()
    try:
        assert _window_has_active_background_work(window) is True
    finally:
        release.set()
        worker.join(3)
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


def test_manual_test_settings_cannot_change_application_data_locator(
    tmp_path, monkeypatch
):
    app = QApplication.instance() or QApplication([])
    environment = ManualTestEnvironment(
        record_count=0,
        diagnostics_root=str(tmp_path / "diagnostics"),
        create_media_files=False,
    ).start()
    view = SettingsView(environment.service)
    try:
        assert view.data_directory_btn.isEnabled() is False
        monkeypatch.setattr(
            QFileDialog,
            "getExistingDirectory",
            lambda *_args, **_kwargs: pytest.fail(
                "临时人工测试不应打开生产数据目录选择器"
            ),
        )
        view._choose_data_directory()
        assert environment.service.manual_test_environment is True
    finally:
        view.close()
        app.processEvents()
        report = environment.close()
        assert report["temp_data_removed"] is True


def test_manual_media_preview_releases_synthetic_file_before_cleanup(tmp_path):
    if not HAS_MULTIMEDIA:
        pytest.skip("Qt Multimedia is unavailable")

    app = QApplication.instance() or QApplication([])
    environment = ManualTestEnvironment(
        record_count=0,
        diagnostics_root=str(tmp_path / "diagnostics"),
        create_media_files=False,
    ).start()
    media_path = environment.media_root / "preview.mp4"
    if not environment._create_synthetic_media_file(media_path):
        environment.close()
        pytest.skip("ffmpeg could not create a valid synthetic video")

    preview = MediaPreviewWidget()
    try:
        preview.set_media(str(media_path))
        app.processEvents()
        assert not preview._player.source().isEmpty()

        preview.set_media(None)
        app.processEvents()
        assert preview._player.source().isEmpty()
    finally:
        preview.close()
        preview.deleteLater()
        app.processEvents()
        report = environment.close()
        assert report["temp_data_removed"] is True


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
