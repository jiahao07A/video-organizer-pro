# -*- coding: utf-8 -*-
"""GUI 评审回归：合成数据库与 Qt 离屏控件，不访问真实配置或素材。"""
import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QThread, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from core.video_organizer_service import SettingsManager, VideoOrganizerService
from gui.main_window import MainWindow
from gui.models.video_table import COL_FILENAME, COL_LIBRARY_ID
from gui.views.material_library import MaterialLibraryView
from gui.views.settings import SettingsView
from gui.views.workstation import WorkstationView


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setattr("core.video_organizer_service.TAG_CONFIG_FILE", str(tmp_path / "tags.json"))
    monkeypatch.setattr("core.video_organizer_service.get_settings_file_path", lambda: str(tmp_path / "settings.json"))
    return VideoOrganizerService(
        settings=SettingsManager.deep_copy_defaults(),
        on_log=lambda _m: None,
        db_path=str(tmp_path / "catalog.db"),
        results_json=str(tmp_path / "results.json"),
        results_csv=str(tmp_path / "results.csv"),
    )


@pytest.fixture
def views(qt_app):
    created = []
    yield created
    for view in created:
        for worker in view.findChildren(QThread):
            worker.wait(5000)
        if hasattr(view, "prefs_controller"):
            view.prefs_controller.cancel()
        view.close()
        view.deleteLater()
    qt_app.processEvents()


def _wait_for(qt_app, predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qt_app.processEvents()
        if predicate():
            return
        QTest.qWait(10)
    assert predicate(), "Qt 状态未在时限内满足条件"


def _insert(service, path, **values):
    record = {"path": str(path), "filename": os.path.basename(str(path)), "status": "pending", "tags": [], "tag_groups": {}}
    record.update(values)
    service.db.upsert_video(record)
    return str(path)


@pytest.mark.skipif(os.name != "nt", reason="Windows 大小写与斜杠路径语义")
def test_removed_path_stays_out_of_scope_and_operation_targets(service, tmp_path):
    folder = tmp_path / "MixedCase"
    removed = _insert(service, folder / "ClipA.mp4")
    kept = _insert(service, folder / "ClipB.mp4")
    service.set_work_scope_paths([str(folder)], scan=False)
    service.remove_videos_from_work_scope([removed.upper()])
    visible = service.resolve_visible_video_paths({}, scope_paths=[str(folder)])
    assert visible == [kept]
    assert service.query_video_page({}, scope_paths=[str(folder)]).total_count == 1
    assert service.resolve_operation_target_paths([removed], visible, scope_paths=service.get_work_scope_video_paths()) == []
    assert service.catalog.count() == 2  # 移出范围不取消入库。


@pytest.mark.skipif(os.name != "nt", reason="Windows 文件范围路径语义")
def test_file_scope_matches_case_and_slash_variants(service, tmp_path):
    path = _insert(service, str(tmp_path / "MixedClip.mp4").replace("\\", "/"))
    assert service.resolve_visible_video_paths({}, scope_paths=[path.upper().replace("/", "\\")]) == [path]


def test_empty_scope_is_not_the_whole_library(service, tmp_path):
    _insert(service, tmp_path / "outside.mp4")
    assert service.query_video_page({}, scope_paths=[]).total_count == 0
    assert service.resolve_visible_video_paths({}, scope_paths=[]) == []
    assert service.query_video_page({}, scope_paths=None).total_count == 1


def test_scope_prefix_does_not_match_a_sibling(service, tmp_path):
    inside = _insert(service, tmp_path / "scope" / "in.mp4")
    _insert(service, tmp_path / "scope-other" / "out.mp4")
    assert service.resolve_visible_video_paths({}, scope_paths=[str(tmp_path / "scope")]) == [inside]


@pytest.mark.parametrize("status_filter", ["未命名", "unnamed", "not_renamed"])
def test_unnamed_status_expands_to_pending_and_analyzed(service, tmp_path, status_filter):
    for name, status in [("a", "pending"), ("b", "analyzed"), ("c", "renamed"), ("d", "")]:
        _insert(service, tmp_path / f"{name}.mp4", status=status)
    params = {"analysis_statuses": [status_filter]}
    assert service.query_video_page(params).total_count == 3
    params["analysis_statuses"].append("已重命名")
    assert service.query_video_page(params).total_count == 4


def test_duplicate_filter_uses_nonempty_content_hashes(service, tmp_path):
    first = _insert(service, tmp_path / "one.mp4", file_hash="same", phash="frame", category="A-Roll")
    second = _insert(service, tmp_path / "two.mp4", file_hash="same", phash="frame", category="B-Roll")
    _insert(service, tmp_path / "unique.mp4", file_hash="unique", phash="frame")
    _insert(service, tmp_path / "blank1.mp4", file_hash="")
    _insert(service, tmp_path / "blank2.mp4", file_hash="")
    _insert(service, tmp_path / "missing.mp4", file_hash=None)
    params = {"only_dup": True}
    assert set(service.resolve_visible_video_paths(params)) == {first, second}
    assert service.query_video_page(params, limit=1).total_count == 2
    assert service.query_video_page({**params, "categories": ["A-Roll"]}).total_count == 1
    assert service.query_video_page(params, scope_paths=[first]).total_count == 1


@pytest.mark.parametrize("view_type", [MaterialLibraryView, WorkstationView])
def test_header_sort_reloads_and_keeps_order_across_pages(service, tmp_path, qt_app, views, view_type):
    for name in ["b.mp4", "a.mp4", "c.mp4"]:
        _insert(service, tmp_path / name)
    service.set_work_scope_paths([str(tmp_path)], scan=False)
    view = view_type(service)
    views.append(view)
    view._page_limit = 2
    view.load_data()
    _wait_for(qt_app, lambda: not view._page_loading)
    view.table_view.horizontalHeader().setSortIndicator(COL_FILENAME, Qt.AscendingOrder)
    _wait_for(qt_app, lambda: view._order_by == "filename" and not view._page_loading and not view._query_pending)
    assert [r["filename"] for r in view.model.videos] == ["a.mp4", "b.mp4"]
    view._load_query_page(reset=False)
    _wait_for(qt_app, lambda: not view._page_loading)
    assert [r["filename"] for r in view.model.videos] == ["a.mp4", "b.mp4", "c.mp4"]
    view.table_view.horizontalHeader().setSortIndicator(COL_LIBRARY_ID, Qt.DescendingOrder)
    _wait_for(qt_app, lambda: view._order_by == "id" and not view._page_loading and not view._query_pending)
    assert [r["filename"] for r in view.model.videos] == ["c.mp4", "a.mp4"]
    assert view.proxy_model.sortColumn() == -1  # 不能只对已加载页做 Qt 本地排序。


@pytest.mark.parametrize("view_type", [MaterialLibraryView, WorkstationView])
def test_selection_loads_complete_detail_without_blank_transcription(service, tmp_path, qt_app, views, view_type):
    path = _insert(service, tmp_path / "clip.mp4", transcription="existing transcript")
    view = view_type(service)
    views.append(view)
    view.model.update_data(service.query_video_page().rows)
    assert "transcription" not in view.model.videos[0]
    view.table_view.selectRow(0)
    _wait_for(qt_app, lambda: view.detail_panel.save_btn.isEnabled())
    assert view.detail_panel.current_video["path"] == path
    assert view.detail_panel.transcript_input.toPlainText() == "existing transcript"


def test_autosave_timer_emits_success_and_clears_pending_status(service, qt_app, views):
    view = SettingsView(service)
    views.append(view)
    view.prefs_controller._timer.setInterval(30)
    saved = []
    view.prefs_controller.saved.connect(saved.append)
    view.font_spin.setValue(19)
    _wait_for(qt_app, lambda: not view.prefs_controller._timer.isActive())
    assert saved == ["界面偏好已保存"]
    assert view.save_status_label.text() == "界面偏好已保存"


def test_autosaved_prefs_apply_to_main_window_without_reloading_ai(service, qt_app, views, monkeypatch):
    window = MainWindow(service)
    views.append(window)
    settings = window._ensure_page(3)
    reloads = []
    monkeypatch.setattr(service, "reload_ai_from_settings", lambda: reloads.append(True))
    settings.theme_combo.setCurrentIndex(1)
    settings.font_spin.setValue(19)
    settings.sidebar_spin.setValue(300)
    settings.default_view_combo.setCurrentIndex(1)
    settings.detail_expanded_cb.setChecked(False)
    settings.prefs_controller.flush()
    assert "font-size: 19px" in window.styleSheet()
    assert window.sidebar_container.width() == 300
    assert window.workstation_page.view_stack.currentWidget() is window.workstation_page.card_view
    assert window.workstation_page.detail_panel.isHidden()
    assert reloads == []


def test_remember_scope_autosave_snapshots_current_paths(service, tmp_path, views):
    folder = str(tmp_path / "scope")
    service.set_work_scope_paths([folder], scan=False)
    view = SettingsView(service)
    views.append(view)
    view.remember_scope_cb.setChecked(True)
    view.prefs_controller.flush()
    assert service.settings["ui_preferences"]["last_work_scope"] == [folder]


def test_sensitive_controls_do_not_start_autosave(service, views):
    view = SettingsView(service)
    views.append(view)
    view.api_key_input.setText("test-unsaved-key")
    assert not view.prefs_controller._timer.isActive()


def test_empty_scope_invalidates_inflight_page(service, tmp_path, views, monkeypatch):
    from core.video_catalog import VideoPage

    path = _insert(service, tmp_path / "clip.mp4")
    service.set_work_scope_paths([str(tmp_path)], scan=False)
    callbacks = []
    monkeypatch.setattr("gui.views.workstation.run_catalog_query", lambda parent, **kw: callbacks.append(kw))
    view = WorkstationView(service)
    views.append(view)
    view.load_data()
    service.clear_work_scope()
    view.load_data()
    callbacks[0]["on_ok"](VideoPage([{"path": path, "filename": "clip.mp4"}], 1, 0, 100))
    assert view.model.rowCount() == 0
    assert view._page_total == 0


@pytest.mark.parametrize("view_type", [MaterialLibraryView, WorkstationView])
def test_late_detail_result_cannot_replace_new_selection(service, tmp_path, views, monkeypatch, view_type):
    first = _insert(service, tmp_path / "one.mp4", transcription="first")
    second = _insert(service, tmp_path / "two.mp4", transcription="second")
    callbacks = []
    module = "material_library" if view_type is MaterialLibraryView else "workstation"
    monkeypatch.setattr(f"gui.views.{module}.run_catalog_query", lambda parent, **kw: callbacks.append(kw))
    view = view_type(service)
    views.append(view)
    view.model.update_data(service.query_video_page(order_by="id", descending=False).rows)
    view.table_view.selectRow(0)
    view.table_view.selectRow(1)
    callbacks[-1]["on_ok"](service.get_video_detail(second))
    callbacks[0]["on_ok"](service.get_video_detail(first))
    assert view.detail_panel.current_video["path"] == second
    assert view.detail_panel.transcript_input.toPlainText() == "second"


def test_autosave_failure_can_retry_without_losing_pending_changes(service, views, monkeypatch):
    view = SettingsView(service)
    views.append(view)
    saved = []
    view.prefs_controller.saved.connect(saved.append)
    view.font_spin.setValue(19)
    original = SettingsManager.save_settings

    def fail(*args):
        raise OSError("simulated write failure")

    monkeypatch.setattr(SettingsManager, "save_settings", fail)
    assert not view.prefs_controller.flush()
    assert "保存失败" in view.save_status_label.text()
    assert not saved
    monkeypatch.setattr(SettingsManager, "save_settings", original)
    assert view.prefs_controller.flush()
    assert saved == ["界面偏好已保存"]


def test_large_exclusion_set_is_one_sql_parameter(service, tmp_path):
    from core.video_catalog import VideoQuery

    kept = _insert(service, tmp_path / "kept.mp4")
    query = VideoQuery(excluded_paths=tuple(str(tmp_path / f"excluded-{i}.mp4") for i in range(1500)))
    assert service.catalog.resolve_visible_paths(query) == [kept]
    assert len(service.catalog._where(query)[1]) == 1
