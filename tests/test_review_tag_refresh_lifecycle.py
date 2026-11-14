"""标签统计的真实 Qt 生命周期与后台线程回归。"""
import threading
import time

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtTest import QTest
from shiboken6 import isValid

from core.video_organizer_service import SettingsManager, VideoOrganizerService
from gui.views.tags_library import TagsView


def wait_until(qt_app, predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qt_app.processEvents()
        if predicate():
            return
        QTest.qWait(10)
    assert predicate(), "标签统计未在期限内达到预期状态"


@pytest.fixture
def tags_service(tmp_path, monkeypatch):
    monkeypatch.setattr("core.video_organizer_service.TAG_CONFIG_FILE", str(tmp_path / "tags.json"))
    monkeypatch.setattr("core.video_organizer_service.get_settings_file_path", lambda: str(tmp_path / "settings.json"))
    service = VideoOrganizerService(settings=SettingsManager.deep_copy_defaults(), on_log=lambda _: None,
        db_path=str(tmp_path / "catalog.db"), results_json=str(tmp_path / "results.json"),
        results_csv=str(tmp_path / "results.csv"))
    service.tag_config = {"tag_groups": [{"id": "mood", "name": "氛围", "tags": ["平静"]}], "global_settings": {}}
    return service


def destroy(view):
    view.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert not isValid(view), "必须真实销毁 QWidget 的 C++ 实例"


def test_destroy_before_refresh_delay_never_starts_database_work(qt_app, tags_service, monkeypatch):
    calls = []
    errors = []
    monkeypatch.setattr(tags_service.db, "refresh_tag_usage_counts", lambda: calls.append(True))
    monkeypatch.setattr(threading, "excepthook", errors.append)
    view = TagsView(tags_service)
    destroy(view)
    QTest.qWait(650)
    assert calls == [], "页面销毁后不应再启动延迟统计任务"
    assert errors == []


def test_destroy_during_refresh_does_not_call_deleted_widget(qt_app, tags_service, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    errors = []

    def refresh():
        entered.set()
        assert release.wait(10), "测试必须释放数据库边界"

    monkeypatch.setattr(tags_service.db, "refresh_tag_usage_counts", refresh)
    monkeypatch.setattr(threading, "excepthook", errors.append)
    view = TagsView(tags_service)
    view.async_refresh_usage_counts()
    worker = view._usage_refresh_thread
    try:
        wait_until(qt_app, entered.is_set)
        destroy(view)
    finally:
        release.set()
        worker.join(timeout=3)
    assert not worker.is_alive()
    qt_app.processEvents()
    assert errors == [], "统计线程结束时不应调用已销毁的 QWidget"


def test_repeated_load_and_refresh_merge_without_overlapping_workers(qt_app, tags_service, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    lock = threading.Lock()
    workers = []
    calls = active = peak = 0

    def refresh():
        nonlocal calls, active, peak
        with lock:
            workers.append(threading.current_thread())
            calls += 1
            active += 1
            peak = max(peak, active)
        entered.set()
        try:
            assert release.wait(10), "测试必须释放数据库边界"
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(tags_service.db, "refresh_tag_usage_counts", refresh)
    view = TagsView(tags_service)
    view.async_refresh_usage_counts()
    first_worker = view._usage_refresh_thread
    try:
        wait_until(qt_app, entered.is_set)
        for _ in range(6):
            view.load_data()
            view.async_refresh_usage_counts()
        QTest.qWait(650)
        assert peak == 1, "快速重复刷新不能并发重算统计"
        assert view._usage_refresh_thread is first_worker, "活跃线程引用必须保留供退出等待"
        release.set()
        wait_until(qt_app, lambda: calls >= 2 and active == 0)
        QTest.qWait(650)
        assert calls == 2, "运行中的多次请求应合并为一次补刷新"
        assert peak == 1
    finally:
        destroy(view)
        release.set()
        for worker in workers:
            worker.join(timeout=3)
        assert not any(worker.is_alive() for worker in workers)


def test_database_error_is_logged_and_later_refresh_updates_real_model(qt_app, tags_service, monkeypatch, caplog, tmp_path):
    tags_service.db.add_tag("mood", "平静")
    for index, tags in enumerate((["平静"], ["平静"], ["其他"])):
        tags_service.db.upsert_video({"path": str(tmp_path / f"synthetic-{index}.mp4"),
                                     "filename": f"synthetic-{index}.mp4", "tags": tags})
    original_refresh = tags_service.db.refresh_tag_usage_counts
    errors = []
    calls = []

    def refresh():
        calls.append(threading.get_ident())
        if len(calls) == 1:
            raise RuntimeError("synthetic database failure")
        original_refresh()

    monkeypatch.setattr(tags_service.db, "refresh_tag_usage_counts", refresh)
    monkeypatch.setattr(threading, "excepthook", errors.append)
    view = TagsView(tags_service)
    workers = []
    try:
        view.async_refresh_usage_counts()
        workers.append(view._usage_refresh_thread)
        wait_until(qt_app, lambda: not workers[0].is_alive())
        assert errors == []
        assert "标签使用统计刷新失败" in caplog.text
        assert view._usage_refresh_thread is workers[0], "结束的线程引用仍可被现有等待器识别"
        assert view.model.get_tag(0).usage_count == 0
        view.async_refresh_usage_counts()
        workers.append(view._usage_refresh_thread)
        wait_until(qt_app, lambda: view.model.get_tag(0).usage_count == 2)
        assert len(calls) == 2
        assert all(ident != threading.get_ident() for ident in calls)
        assert errors == []
    finally:
        for worker in workers:
            worker.join(timeout=3)
        destroy(view)


def test_repeated_loads_before_delay_run_one_refresh(qt_app, tags_service, monkeypatch):
    calls = []
    monkeypatch.setattr(tags_service.db, "refresh_tag_usage_counts", lambda: calls.append(True))
    view = TagsView(tags_service)
    try:
        for _ in range(5):
            view.load_data()
            QTest.qWait(100)
        assert calls == []
        wait_until(qt_app, lambda: len(calls) == 1)
        QTest.qWait(650)
        assert len(calls) == 1
    finally:
        worker = getattr(view, "_usage_refresh_thread", None)
        if worker is not None:
            worker.join(timeout=3)
        destroy(view)
