"""真实同窗页面、后台线程与 XMP 的跨页写入门禁回归。"""
import threading
import time
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox
from shiboken6 import isValid
from xml.etree import ElementTree

from core.manual_test_environment import wait_for_background_work
from core.video_organizer_service import SettingsManager, VideoOrganizerService
from gui.main_window import MainWindow
from gui.workers.io_worker import IoWorker, run_io_job


def wait_until(qt_app, predicate):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        qt_app.processEvents()
        if predicate():
            return
        QTest.qWait(10)
    assert predicate(), "真实后台任务未在期限内完成"


@pytest.fixture
def scene(qt_app, tmp_path, monkeypatch):
    monkeypatch.setattr("core.video_organizer_service.TAG_CONFIG_FILE", str(tmp_path / "tags.json"))
    monkeypatch.setattr("core.video_organizer_service.get_settings_file_path", lambda: str(tmp_path / "settings.json"))
    monkeypatch.setattr("gui.services.update_controller.UpdateController.start_automatic_checks", lambda _: None)
    for name in ("information", "warning"):
        monkeypatch.setattr(QMessageBox, name, lambda *args, **kwargs: None)
    state = SimpleNamespace(prompts=[], io_calls=[], busy=[], on_confirm=lambda: None)

    def confirm(*args, **kwargs):
        state.prompts.append(args[1])
        state.on_confirm()
        return QMessageBox.Yes

    def observed_io(*args, **kwargs):
        worker = run_io_job(*args, **kwargs)
        state.io_calls.append(worker)
        return worker

    monkeypatch.setattr(QMessageBox, "question", confirm)
    monkeypatch.setattr("gui.views.workstation.run_io_job", observed_io)
    state.service = VideoOrganizerService(settings=SettingsManager.deep_copy_defaults(), on_log=lambda _: None,
        db_path=str(tmp_path / "catalog.db"), results_json=str(tmp_path / "results.json"),
        results_csv=str(tmp_path / "results.csv"))
    state.video = tmp_path / "synthetic.mp4"
    state.video.write_bytes(b"synthetic fixture; no user media")
    state.xmp = state.video.with_suffix(".xmp")
    state.sentinel = b"synthetic existing XMP must remain unchanged while busy"
    state.xmp.write_bytes(state.sentinel)
    state.service.db.upsert_video({"path": str(state.video), "filename": state.video.name,
        "status": "analyzed", "summary": "synthetic cross-page summary", "category": "Other", "tags": ["synthetic-tag"]})
    state.service.replace_work_scope([str(state.video)], scan=False)
    state.window = MainWindow(state.service)
    state.workstation = state.window.workstation_page
    state.library = state.window._ensure_page(1)
    assert wait_for_background_work(state.window, timeout=5)
    qt_app.processEvents()

    def start_busy(owner, name):
        entered, release = threading.Event(), threading.Event()

        def job():
            entered.set()
            assert release.wait(20), "测试必须释放后台任务"

        worker = IoWorker(job, owner)
        setattr(owner, name, worker)
        state.busy.append((owner, name, worker, release))
        worker.start()
        assert entered.wait(2) and worker.isRunning()
        return worker

    state.start_busy = start_busy
    yield state
    if state.service._analysis_run_lock.locked():
        state.service._analysis_run_lock.release()
    for owner, name, worker, release in state.busy:
        release.set()
        assert worker.wait(5000), "不得销毁运行中的真实线程"
        if getattr(owner, name, None) is worker:
            setattr(owner, name, None)
    for timer in state.window.findChildren(QTimer):
        timer.stop()
    assert wait_for_background_work(state.window, timeout=5)
    qt_app.processEvents()
    state.window._allow_exit = True
    state.window.close()
    state.window.deleteLater()
    qt_app.processEvents()


@pytest.mark.parametrize("busy_source", ["service_lock", "_io_worker", "worker", "recovery_worker"])
@pytest.mark.parametrize("during_confirmation", [False, True])
def test_workstation_xmp_is_blocked_by_service_or_library_execution(
    scene, qt_app, monkeypatch, busy_source, during_confirmation,
):
    active = []

    def occupy():
        if busy_source == "service_lock":
            assert scene.service._analysis_run_lock.acquire(blocking=False)
        else:
            active.append(scene.start_busy(scene.library, busy_source))

    if during_confirmation:
        scene.on_confirm = occupy
    else:
        occupy()
    scene.workstation.sync_metadata()
    wait_until(qt_app, lambda: getattr(scene.workstation, "_io_worker", None) is None)
    assert scene.xmp.read_bytes() == scene.sentinel, "素材库执行任务或分析锁占用时不得覆盖侧车"
    assert scene.io_calls == [], "忙碌时不得启动新的工作台 IoWorker"
    assert len(scene.prompts) == int(during_confirmation)
    if active:
        assert active[0].isRunning() and getattr(scene.library, busy_source) is active[0]
    else:
        assert scene.service._analysis_run_lock.locked()
    monkeypatch.setattr(scene.workstation, "_resolve_batch_snapshot", lambda: pytest.fail("忙碌时不得继续解析写盘目标"))
    scene.workstation.start_rename(dry_run=False)
    scene.workstation.start_auto_organize()
    assert scene.io_calls == []
    assert scene.xmp.read_bytes() == scene.sentinel


def assert_real_xmp_written(scene, qt_app):
    scene.workstation.sync_metadata()
    wait_until(qt_app, lambda: getattr(scene.workstation, "_io_worker", None) is None)
    assert len(scene.io_calls) == len(scene.prompts) == 1
    assert isinstance(scene.io_calls[0], IoWorker)
    written = scene.xmp.read_bytes()
    assert written != scene.sentinel
    root = ElementTree.fromstring(written)
    assert any(node.text == "synthetic cross-page summary" for node in root.iter())
    assert any(node.text == "synthetic-tag" for node in root.iter())


@pytest.mark.parametrize("owner_name", ["workstation", "library"])
@pytest.mark.parametrize("attribute", ["_detail_worker", "_query_worker"])
def test_read_only_workers_do_not_block_real_xmp_write(scene, qt_app, owner_name, attribute):
    owner = getattr(scene, owner_name)
    active = scene.start_busy(owner, attribute)
    assert_real_xmp_written(scene, qt_app)
    assert active.isRunning() and getattr(owner, attribute) is active


def test_idle_workstation_still_writes_real_xmp(scene, qt_app):
    assert_real_xmp_written(scene, qt_app)


def test_deleted_library_worker_wrapper_does_not_block_real_xmp_write(scene, qt_app):
    worker = IoWorker(lambda: None, scene.library)
    scene.library.recovery_worker = worker
    worker.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert not isValid(worker)
    try:
        assert_real_xmp_written(scene, qt_app)
    finally:
        scene.library.recovery_worker = None
