# -*- coding: utf-8 -*-
"""设置目录、防抖控制器与设置页：可搜索导航与统一防抖自动保存。

需要 Qt，使用 offscreen 平台，不依赖真实显示器。
"""
import json
import copy
from pathlib import Path

import pytest


pytest.importorskip("PySide6")

from PySide6.QtWidgets import QMessageBox  # noqa: E402

from core.video_organizer_service import (  # noqa: E402
    SettingsManager,
    VideoOrganizerService,
)
from gui.models import settings_catalog as catalog  # noqa: E402
from gui.views.settings import SettingsView  # noqa: E402


@pytest.fixture
def service(tmp_path, monkeypatch, qt_app):
    monkeypatch.setattr("core.video_organizer_service.TAG_CONFIG_FILE", str(tmp_path / "tags.json"))
    monkeypatch.setattr("core.video_organizer_service.get_settings_file_path", lambda: str(tmp_path / "settings.json"))
    svc = VideoOrganizerService(
        settings=SettingsManager.deep_copy_defaults(),
        on_log=lambda _m: None,
        db_path=str(tmp_path / "t.db"),
        results_json=str(tmp_path / "r.json"),
        results_csv=str(tmp_path / "c.csv"),
    )
    yield svc
    for view in qt_app.topLevelWidgets():
        if isinstance(view, SettingsView) and view.service is svc:
            view.prefs_controller.cancel()
            view.close()
            view.deleteLater()
    from PySide6.QtCore import QCoreApplication, QEvent
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


@pytest.fixture
def silent_boxes(monkeypatch):
    for name in ("information", "warning", "critical"):
        monkeypatch.setattr(
            QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.Ok)
        )


# —— 目录 ——

def test_catalog_categories_are_task_oriented():
    ids = [c.id for c in catalog.list_categories()]
    assert ids == ["everyday", "ai_connections", "ai_models", "prompts", "video_analysis", "failure_handling", "monitoring", "naming", "system"]


def test_catalog_search_finds_by_label_and_keyword():
    labels = [m.entry.label for m in catalog.search_entries("主题")]
    assert "主题" in labels
    assert any(m.entry.label == "API Key" for m in catalog.search_entries("API Key"))
    assert any("重命名" in m.entry.label for m in catalog.search_entries("重命名"))
    assert any(m.entry.label == "任务模型路由" for m in catalog.search_entries("路由"))


def test_catalog_empty_query_returns_all_entries():
    assert len(catalog.search_entries("")) == len(catalog.SETTINGS_ENTRIES)


def test_catalog_only_one_rename_pattern_entry():
    """重命名模式在目录里只有一个真实入口。"""
    rename_entries = [
        e for e in catalog.SETTINGS_ENTRIES if e.widget_key == "rename_pattern"
    ]
    assert len(rename_entries) == 1


def test_catalog_marks_ordinary_prefs_autosave():
    for widget_key in (
        "theme", "font_size", "default_view", "sidebar_width",
        "detail_panel_expanded", "remember_work_scope",
    ):
        entry = catalog.get_entry_by_widget(widget_key)
        assert entry is not None
        assert entry.save_mode == catalog.AUTOSAVE
    assert catalog.get_entry_by_widget("api_key").save_mode == catalog.MANUAL


# —— 视图：可搜索导航 ——

def test_view_has_searchable_catalog(qt_app, service):
    view = SettingsView(service)
    view._on_settings_search("主题")
    assert view.settings_search_results.model().rowCount() >= 1
    assert view.focus_setting("theme") is True
    assert view.tabs.currentIndex() == 0  # 界面与体验


def test_view_search_jump(qt_app, service):
    view = SettingsView(service)
    view.settings_search_input.setText("API Key")
    item = view.settings_search_results.model().index(0,0)
    view._on_search_result_activated(item)
    assert view.tabs.currentIndex() == 1


def test_dynamic_search_targets_new_page_indices(qt_app, service):
    view = SettingsView(service)
    view._on_settings_search("局部提示词")
    prompt_item = view.settings_search_results.model().index(0, 0)
    view._on_search_result_activated(prompt_item)
    assert view.tabs.currentIndex() == 3

    view._on_settings_search("抽帧")
    processing_item = view.settings_search_results.model().index(0, 0)
    view._on_search_result_activated(processing_item)
    assert view.tabs.currentIndex() == 4


def test_left_navigation_groups_settings_by_user_task(qt_app, service):
    view = SettingsView(service)
    assert [view.tabs.tabText(i) for i in range(view.tabs.count())] == [
        "界面与工作区", "AI 连接", "AI 模型分配", "提示词与标签", "视频分析",
        "失败处理", "监控默认值", "命名与导出", "数据与维护",
    ]
    row = next(row for row, page in view._nav_row_to_page.items() if page == 2)
    view.settings_nav.setCurrentRow(row)
    assert view.tabs.currentIndex() == 2


def test_sensitive_settings_use_unified_autosave(qt_app, service, tmp_path, monkeypatch):
    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(
        "core.video_organizer_service.get_settings_file_path",
        lambda: str(settings_path),
    )
    view = SettingsView(service)
    view.api_key_input.setText("sk-auto-save")
    assert service.settings["api"]["key"] == "sk-auto-save"
    view.prefs_controller.flush()
    assert json.loads(settings_path.read_text(encoding="utf-8"))["api"]["key"] == "sk-auto-save"


def test_ai_settings_split_connections_from_model_assignment(qt_app, service):
    view = SettingsView(service)
    assert view.tabs.widget(1).isAncestorOf(view.api_key_input)
    assert view.tabs.widget(2).isAncestorOf(view.cls_model_input)
    assert not view.tabs.widget(1).isAncestorOf(view.cls_model_input)
    assert view._route_widgets["video_classification"]["model"].combo.lineEdit().placeholderText().startswith("沿用连接默认模型")


def test_provider_presets_fill_new_connection_defaults(qt_app, service):
    view = SettingsView(service)
    view.provider_preset_combo.setCurrentIndex(view.provider_preset_combo.findData("openrouter"))
    before = view.provider_list.count()
    view._on_add_provider()
    assert view.provider_list.count() == before + 1
    provider = next(p for p in view.service.settings["model_providers"] if p["base_url"] == "https://openrouter.ai/api/v1")
    assert provider["display_name"] == "OpenRouter"
    assert provider["base_url"] == "https://openrouter.ai/api/v1"


def test_provider_profile_fields_are_saved_by_unified_controller(qt_app, service):
    view = SettingsView(service)
    view.provider_name_input.setText("团队 OpenAI")
    view.cls_model_input.setText("vision-model")
    view.tag_model_input.setText("tag-model")
    view.desc_model_input.setText("description-model")
    view.prefs_controller.flush()
    provider = next(
        p for p in service.settings["model_providers"]
        if p["id"] == view._selected_provider_id()
    )
    assert provider["display_name"] == "团队 OpenAI"
    assert provider["models"]["video_classification"] == "vision-model"
    assert provider["models"]["tag_generation"] == "tag-model"
    assert provider["models"]["content_description"] == "description-model"


def test_ordinary_pref_autosaves_and_reports_status(qt_app, service):
    view = SettingsView(service)
    saved = []
    view.prefs_controller.saved.connect(saved.append)

    view.theme_combo.setCurrentIndex(1)
    assert service.settings["ui_preferences"]["theme"] == "light"
    view.prefs_controller.flush()
    assert saved  # 状态已反馈
    assert view.save_status_label.text() != ""


def test_composite_thumbnail_pref_autosaves(qt_app, service):
    view = SettingsView(service)
    view.thumb_w_spin.setValue(200)
    view.prefs_controller.flush()
    assert service.settings["ui_preferences"]["thumbnail_size"] == [200, 90]


def test_autosave_writes_settings_file(qt_app, service, tmp_path, monkeypatch):
    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(
        "core.video_organizer_service.get_settings_file_path",
        lambda: str(settings_path),
    )
    view = SettingsView(service)
    view.font_spin.setValue(19)
    view.prefs_controller.flush()
    assert settings_path.exists()
    raw = json.loads(settings_path.read_text(encoding="utf-8"))
    assert raw["ui_preferences"]["font_size"] == 19


# —— 视图：敏感配置明确保存 ——

def test_api_key_survives_restart_after_debounced_autosave(qt_app, service):
    from tests.support.qt_http import wait
    settings_path = Path(service._data_paths.settings)
    view = SettingsView(service)
    view.api_key_input.setText("sk-sensitive")
    wait(qt_app, lambda: settings_path.is_file() and not view.prefs_controller._dirty)
    assert json.loads(settings_path.read_text(encoding="utf-8"))["api"]["key"] == "sk-sensitive"
    restarted = VideoOrganizerService(db_path=service.db.db_path)
    assert restarted.settings["api"]["key"] == "sk-sensitive"


# —— 视图：重命名入口唯一 ——

def test_rename_pattern_has_single_editable_entry(qt_app, service):
    view = SettingsView(service)
    assert not hasattr(view, "filename_tmpl")
    assert view.tabs.widget(7).isAncestorOf(view.pattern_input)
    view.pattern_input.setText("{category}-{summary}")
    view.prefs_controller.flush()
    assert service.settings["rename_pattern"] == "{category}-{summary}"


# —— 视图：预览不悄悄持久化 local_prompt ——

def test_prompt_edit_auto_saves_local_prompt(qt_app, service):
    tag_config_path = Path(service._tag_config_path)
    service.save_tag_config(copy.deepcopy(service.tag_config))
    view = SettingsView(service)
    assert view._local_prompt_edits
    gid = next(iter(view._local_prompt_edits))
    view._local_prompt_edits[gid].setPlainText("UNSAVED-PREVIEW-XYZ")
    view.refresh_prompt_preview()
    view.prefs_controller.flush()
    assert service.tag_config["tag_groups"]
    group = next(item for item in service.tag_config["tag_groups"] if item["id"] == gid)
    assert group["rules"]["local_prompt"] == "UNSAVED-PREVIEW-XYZ"
    assert b"UNSAVED-PREVIEW-XYZ" in tag_config_path.read_bytes()


def test_tag_ai_prompt_auto_saves(qt_app, service):
    view = SettingsView(service)
    edit = view._tag_ai_prompt_edits["pending_tag_ai"]
    edit.setPlainText("TAG-AI-AUTO-SAVE")
    view.prefs_controller.flush()
    assert service.settings["tag_ai_prompts"]["pending_tag_ai"] == "TAG-AI-AUTO-SAVE"


def test_model_picker_changes_schedule_unified_autosave(qt_app, service):
    view = SettingsView(service)
    view.cls_model_input.combo.addItems(["vision-a", "vision-b"])
    view.cls_model_input.combo.setCurrentText("vision-b")
    assert view.prefs_controller._timer.isActive()
    view.prefs_controller.flush()
    provider = next(
        p for p in service.settings["model_providers"]
        if p["id"] == view._selected_provider_id()
    )
    assert provider["models"]["video_classification"] == "vision-b"


def test_prompt_category_reset_restores_prompts_only(qt_app, service, monkeypatch):
    view = SettingsView(service)
    service.settings["tag_ai_prompts"]["pending_tag_ai"] = "CUSTOM"
    service.tag_config.setdefault("global_settings", {})["system_prompt"] = "CUSTOM SYSTEM"
    gid = next(iter(view._local_prompt_edits), None)
    if gid:
        for group in service.tag_config["tag_groups"]:
            if str(group.get("id")) == str(gid):
                group.setdefault("rules", {})["local_prompt"] = "CUSTOM LOCAL"
    monkeypatch.setattr(
        "gui.views.settings.QMessageBox.question",
        lambda *args, **kwargs: QMessageBox.Yes,
    )
    view._reset_category_defaults(3)
    assert service.settings["tag_ai_prompts"]["pending_tag_ai"] != "CUSTOM"
    assert service.tag_config["global_settings"]["system_prompt"] != "CUSTOM SYSTEM"
    if gid:
        group = next(g for g in service.tag_config["tag_groups"] if str(g.get("id")) == str(gid))
        assert group["rules"]["local_prompt"] == ""


def test_reset_all_rebuilds_workbench_navigation_and_search(qt_app, service, monkeypatch):
    view = SettingsView(service)
    monkeypatch.setattr(
        "gui.views.settings.QMessageBox.question",
        lambda *args, **kwargs: QMessageBox.Yes,
    )
    monkeypatch.setattr(
        "gui.views.settings.QMessageBox.information",
        lambda *args, **kwargs: QMessageBox.Ok,
    )
    view.reset_to_defaults()
    assert [view.tabs.tabText(i) for i in range(view.tabs.count())] == [
        "界面与工作区", "AI 连接", "AI 模型分配", "提示词与标签", "视频分析",
        "失败处理", "监控默认值", "命名与导出", "数据与维护",
    ]
    assert len(view._nav_row_to_page) == 9
    assert len([view.settings_nav.item(i) for i in range(view.settings_nav.count())
                if view.settings_nav.item(i).data(256) is None]) == 4
    view._on_settings_search("API Key")
    item = view.settings_search_results.model().index(0, 0)
    view._on_search_result_activated(item)
    assert view.tabs.currentIndex() == 1
    view.prefs_controller.flush()


def test_responsibility_catalog_locations_are_complete(qt_app, service):
    view = SettingsView(service)
    assert len({entry.widget_key for entry in catalog.SETTINGS_ENTRIES}) == len(catalog.SETTINGS_ENTRIES)
    for entry in catalog.SETTINGS_ENTRIES:
        assert entry.page_id and entry.card_id and entry.scope and entry.storage_target and entry.effective_when
        widget = view._search_widget_map[entry.widget_key]
        assert view.tabs.widget(entry.tab_index).isAncestorOf(widget), entry.widget_key
    assert view.tabs.widget(7).isAncestorOf(view.auto_xmp_cb)


@pytest.mark.parametrize("remember_scope", [False, True])
def test_autosave_debounces_edits_for_800ms(qt_app, service, monkeypatch, remember_scope):
    from PySide6.QtTest import QTest
    view = SettingsView(service)
    writes = []
    monkeypatch.setattr(SettingsManager, "save_settings", lambda *a: writes.append(copy.deepcopy(service.settings)))
    if remember_scope:
        view.remember_scope_cb.setChecked(True)
    view.font_spin.setValue(17)
    QTest.qWait(450)
    assert not writes
    view.font_spin.setValue(18)
    QTest.qWait(450)
    assert not writes
    QTest.qWait(450)
    assert len(writes) == 1
    assert writes[0]["ui_preferences"]["font_size"] == 18


@pytest.mark.parametrize("field", ["api_key_input", "api_url_input", "system_prompt_edit"])
def test_text_blur_flushes_without_waiting(qt_app, service, monkeypatch, field):
    from PySide6.QtCore import QEvent
    view = SettingsView(service)
    writes = []
    monkeypatch.setattr(SettingsManager, "save_settings", lambda *a: writes.append("settings"))
    monkeypatch.setattr(service, "save_tag_config", lambda *a: writes.append("tag_config"))
    edit = getattr(view, field)
    if field == "system_prompt_edit":
        edit.setPlainText("新的分析角色")
    else:
        edit.setText("https://example.test/v1" if field == "api_url_input" else "sk-test-hidden")
    assert view.prefs_controller._dirty
    qt_app.sendEvent(edit, QEvent(QEvent.FocusOut))
    assert writes
    assert not view.prefs_controller._dirty


@pytest.mark.parametrize("failing_target", ["settings", "tag_config"])
def test_storage_failures_are_independent_and_redacted(qt_app, service, monkeypatch, failing_target):
    view = SettingsView(service)
    secret = "sk-PRIVATE-NEVER-DISPLAY"
    view.api_key_input.setText(secret)
    view.system_prompt_edit.setPlainText("保留这份编辑")
    writes = []
    def settings_write(*args):
        writes.append("settings")
        if failing_target == "settings":
            raise OSError(secret)
    def tags_write(*args):
        writes.append("tag_config")
        if failing_target == "tag_config":
            return False
        return True
    monkeypatch.setattr(SettingsManager, "save_settings", settings_write)
    monkeypatch.setattr(service, "save_tag_config", tags_write)
    assert not view.prefs_controller.flush_now()
    assert set(writes) == {"settings", "tag_config"}
    assert view.prefs_controller._targets[failing_target]["state"] == "failed"
    other = "settings" if failing_target == "tag_config" else "tag_config"
    assert view.prefs_controller._targets[other]["state"] == "saved"
    assert secret not in view.save_status_label.text()
    assert view.api_key_input.text() == secret
    assert view.system_prompt_edit.toPlainText() == "保留这份编辑"
    monkeypatch.setattr(SettingsManager, "save_settings", lambda *a: writes.append("settings"))
    monkeypatch.setattr(service, "save_tag_config", lambda *a: writes.append("tag_config"))
    writes.clear()
    assert view.prefs_controller.flush_now()
    assert writes == [failing_target]


def test_exports_use_authoritative_tag_config_and_reopen(qt_app, service):
    view = SettingsView(service)
    view.xmp_lang_combo.setCurrentText("en")
    view.ale_columns_edit.setText("Name, Summary")
    assert view.prefs_controller.flush_now()
    stored = json.loads(Path(service._tag_config_path).read_text(encoding="utf-8"))
    assert stored["global_settings"]["language"] == "en"
    assert stored["global_settings"]["export_schemes"]["ale_columns"] == ["Name", "Summary"]
    reopened = SettingsView(service)
    assert reopened.xmp_lang_combo.currentText() == "en"
    assert reopened.ale_columns_edit.text() == "Name, Summary"


def test_local_analysis_reset_preserves_retry_watch_and_xmp(qt_app, service):
    from core.watch_config import normalize_watch_config
    service.settings["processing"]["media_watch"] = normalize_watch_config({"directories": [{"id": "one", "path": "D:/watch", "enabled": False, "stability_seconds": 90}]})
    view = SettingsView(service)
    view.call_extra_spin.setValue(4)
    view.auto_xmp_cb.setChecked(True)
    view.workers_spin.setValue(30)
    previous = copy.deepcopy(service.settings["processing"])
    view._suppress_provider_signals = True
    view._apply_reset_group("analysis_pipeline")
    view._suppress_provider_signals = False
    view._schedule_settings_save()
    assert view.workers_spin.value() == 16
    assert service.settings["processing"]["analysis_retry"] == previous["analysis_retry"]
    assert service.settings["processing"]["media_watch"] == previous["media_watch"]
    assert service.settings["processing"]["auto_sync_xmp_after_analysis"] is True


def test_reset_all_preserves_connections_directories_and_data(qt_app, service, monkeypatch):
    from core.watch_config import DEFAULTS, normalize_watch_config
    service.settings["processing"]["media_watch"] = normalize_watch_config({"directories": [{"id": "one", "path": "D:/watch", "enabled": False, "stability_seconds": 90}]})
    service.settings["data_directory_marker"] = "KEEP"
    service.settings["ui_preferences"]["font_size"] = 22
    view = SettingsView(service)
    view.api_key_input.setText("sk-KEEP")
    view.api_url_input.setText("https://keep.example/v1")
    view.workers_spin.setValue(40)
    assert view.prefs_controller.flush_now()
    provider = copy.deepcopy(service.settings["model_providers"][0])
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.Yes)
    view.reset_to_defaults()
    assert view.prefs_controller.flush_now()
    current = service.settings["model_providers"][0]
    for key in ("id", "display_name", "api_key", "base_url"):
        assert current[key] == provider[key]
    task = service.settings["processing"]["media_watch"]["directories"][0]
    assert task["id"] == "one" and task["enabled"] is False
    assert all(task[key] is None for key in DEFAULTS)
    assert service.settings["data_directory_marker"] == "KEEP"
    assert service.settings["ui_preferences"]["font_size"] == view.font_spin.value() == 14
    assert service.settings["processing"]["max_workers"] == view.workers_spin.value() == 16
    assert view.tabs.count() == 9


def test_monitor_task_edit_inherits_and_never_writes_global(qt_app, service, monkeypatch, tmp_path):
    from core.watch_config import normalize_watch_config, effective_directory_options
    from gui.views.media_watch_advanced import MediaWatchSettingsView
    config = normalize_watch_config({"stability_seconds": 30, "directories": [{"id": "one", "path": str(tmp_path), "enabled": True}]})
    service.settings["processing"]["media_watch"] = config
    panel = MediaWatchSettingsView(service, mode="task", task_id="one")
    assert "start_with_windows" not in panel.fields
    panel.inherit["stability_seconds"].setChecked(False)
    panel.fields["stability_seconds"].setValue(50)
    assert panel.controller.flush_now()
    config = service.settings["processing"]["media_watch"]
    assert config["stability_seconds"] == 30
    assert config["directories"][0]["stability_seconds"] == 50
    panel.inherit["stability_seconds"].setChecked(True)
    assert panel.controller.flush_now()
    assert effective_directory_options(service.settings["processing"]["media_watch"], str(tmp_path))["stability_seconds"] == 30
    before = copy.deepcopy(service.settings["processing"]["media_watch"])
    missing = MediaWatchSettingsView(service, mode="task", task_id="deleted")
    missing.fields["stability_seconds"].setValue(99)
    assert missing._save() is False
    assert service.settings["processing"]["media_watch"] == before
    panel.controller.cancel()
    missing.controller.cancel()
    panel.deleteLater()
    missing.deleteLater()


def test_monitor_global_card_reset_keeps_task_override(qt_app, service, monkeypatch):
    from core.watch_config import normalize_watch_config
    service.settings["processing"]["media_watch"] = normalize_watch_config({"stability_seconds": 90, "directories": [{"id": "one", "path": "D:/watch", "stability_seconds": 99}]})
    view = SettingsView(service)
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.Yes)
    view.media_watch_page._reset_fields({"stability_seconds"})
    assert view.prefs_controller.flush_now()
    config = service.settings["processing"]["media_watch"]
    assert config["stability_seconds"] == 30
    assert config["directories"][0]["stability_seconds"] == 99


def test_connection_test_does_not_change_routes_or_selected_models(qt_app, service, monkeypatch):
    from gui.services.model_catalog import ModelCatalog
    from tests.support.qt_http import wait
    from tests.support.review_sdk_server import sdk_server
    model_catalog = ModelCatalog()
    monkeypatch.setattr("gui.widgets.model_picker.model_catalog", lambda: model_catalog)
    monkeypatch.setattr("gui.services.model_catalog.model_catalog", lambda: model_catalog)
    view = SettingsView(service)
    view._on_add_provider()
    assert view._selected_provider_id() != service.settings["current_provider_id"]
    before_routes = copy.deepcopy(service.settings["task_model_routing"])
    before_default = service.settings["current_provider_id"]
    with sdk_server() as (url, calls):
        view.api_url_input.setText(url)
        view.api_key_input.setText("only-selected-key")
        before_models = copy.deepcopy(service.settings["model_providers"])
        view.connection_test_btn.click()
        wait(qt_app, view.connection_test_btn.isEnabled)
        assert calls == [("/v1/models", "Bearer only-selected-key")]
    assert service.settings["task_model_routing"] == before_routes
    assert service.settings["model_providers"] == before_models
    assert service.settings["current_provider_id"] == before_default
    assert "2" in view.connection_test_status_label.text()
    assert view.cls_model_input.combo.count() == 2
    assert view.cls_model_input.combo.itemText(0) == "candidate-a"


def test_saved_connection_name_updates_labels_without_reloading_fields(qt_app, service):
    from PySide6.QtCore import Qt
    view = SettingsView(service)
    provider_id = view._selected_provider_id()
    view.cls_model_input.setText("custom-model")
    view.api_key_visibility_btn.setChecked(True)
    route_selections = {key: widgets["provider"].currentData() for key, widgets in view._route_widgets.items()}
    view.provider_name_input.setText("重命名连接")
    assert view.prefs_controller.flush_now()
    assert view.provider_list.currentItem().data(Qt.UserRole) == provider_id
    assert view.provider_list.currentItem().text().startswith("重命名连接")
    assert "重命名连接" in view.current_provider_label.text()
    assert view.model_provider_combo.currentText() == "重命名连接"
    for key, widgets in view._route_widgets.items():
        combo = widgets["provider"]
        assert combo.itemText(combo.findData(provider_id)) == "重命名连接"
        assert combo.currentData() == route_selections[key]
    assert view.cls_model_input.text() == "custom-model"
    assert view.api_key_visibility_btn.isChecked()
    assert not view.prefs_controller._dirty


class _MaintenanceMonitor:
    def __init__(self, *, paused=False, busy=False, busy_on_stop=False):
        from types import SimpleNamespace
        self._paused_all = paused
        self.analysis_running = busy
        self.busy_on_stop = busy_on_stop
        self.stopped = False
        self.starts = []
        self._thread = SimpleNamespace(is_alive=lambda: not self.stopped)

    def stop(self):
        self.stopped = True
        self.analysis_running = self.analysis_running or self.busy_on_stop

    def start(self, *, paused=False):
        self.starts.append(paused)
        self.stopped = False


@pytest.mark.parametrize("operation", ["_backup_configuration", "_restore_configuration"])
def test_maintenance_blocks_active_monitor_analysis(qt_app, service, silent_boxes, monkeypatch, operation):
    view = SettingsView(service)
    view.media_monitor = _MaintenanceMonitor(busy=True)
    calls = []
    monkeypatch.setattr(service, "backup_configuration", lambda: calls.append("backup"))
    monkeypatch.setattr(service, "list_backups", lambda: calls.append("list"))
    getattr(view, operation)()
    assert calls == []
    assert not view.media_monitor.stopped


@pytest.mark.parametrize("paused", [False, True])
@pytest.mark.parametrize("outcome", ["cancel_selection", "cancel_confirmation", "failure", "success", "no_backups"])
def test_restore_stops_monitor_and_only_restarts_without_success(
    qt_app, service, silent_boxes, monkeypatch, paused, outcome,
):
    from PySide6.QtWidgets import QApplication, QInputDialog
    view = SettingsView(service)
    monitor = view.media_monitor = _MaintenanceMonitor(paused=paused)
    calls = []

    def backups():
        assert monitor.stopped
        return [] if outcome == "no_backups" else ["synthetic-backup"]

    def restore(chosen):
        assert monitor.stopped
        assert chosen == "synthetic-backup"
        calls.append("restore")
        return outcome == "success"

    monkeypatch.setattr(service, "list_backups", backups)
    monkeypatch.setattr(service, "restore_configuration", restore)
    monkeypatch.setattr(QInputDialog, "getItem", lambda *a, **kw: ("synthetic-backup", outcome != "cancel_selection"))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: QMessageBox.No if outcome == "cancel_confirmation" else QMessageBox.Yes)
    monkeypatch.setattr(QApplication, "quit", lambda *a: calls.append("quit"))
    view._restore_configuration()
    if outcome == "success":
        assert calls == ["restore", "quit"]
        assert monitor.stopped
        assert monitor.starts == []
    else:
        assert calls == (["restore"] if outcome == "failure" else [])
        assert monitor.starts == [paused]


def test_backup_stops_monitor_and_preserves_pause(qt_app, service, silent_boxes, monkeypatch):
    view = SettingsView(service)
    monitor = view.media_monitor = _MaintenanceMonitor(paused=True)
    calls = []

    def backup():
        assert monitor.stopped
        calls.append("backup")
        return "synthetic-backup"

    monkeypatch.setattr(service, "backup_configuration", backup)
    view._backup_configuration()
    assert calls == ["backup"]
    assert monitor.starts == [True]


def test_restore_rechecks_monitor_after_stopping_scan(qt_app, service, silent_boxes, monkeypatch):
    view = SettingsView(service)
    monitor = view.media_monitor = _MaintenanceMonitor(busy_on_stop=True)
    calls = []
    monkeypatch.setattr(service, "list_backups", lambda: calls.append("list"))
    view._restore_configuration()
    assert calls == []
    assert monitor.starts == [False]


@pytest.mark.parametrize("active_worker", ["_thread", "_observer"])
def test_restore_rejects_monitor_threads_that_did_not_stop(
    qt_app, service, silent_boxes, monkeypatch, active_worker,
):
    from types import SimpleNamespace
    view = SettingsView(service)
    monitor = view.media_monitor = _MaintenanceMonitor()
    setattr(monitor, active_worker, SimpleNamespace(is_alive=lambda: True))
    calls = []
    monkeypatch.setattr(service, "list_backups", lambda: calls.append("list"))
    view._restore_configuration()
    assert calls == []
    assert monitor.stopped
    assert monitor.starts == []


@pytest.mark.parametrize("size", [(1280, 720), (1024, 768)])
def test_responsibility_layout_scroll_footer_focus_and_screenshots(qt_app, service, size):
    from PySide6.QtWidgets import QWidget, QHBoxLayout, QScrollArea, QGroupBox
    from gui.styles import get_main_style
    host = QWidget()
    from PySide6.QtGui import QFontDatabase
    font_id = QFontDatabase.addApplicationFont("C:/Windows/Fonts/msyh.ttc")
    host.setStyleSheet(get_main_style(theme="dark").replace(
        '"Segoe UI", "Microsoft YaHei", sans-serif', '"Microsoft YaHei"'))
    outer = QHBoxLayout(host)
    outer.setContentsMargins(0, 0, 0, 0)
    app_sidebar = QWidget()
    app_sidebar.setFixedWidth(180)
    outer.addWidget(app_sidebar)
    view = SettingsView(service)
    outer.addWidget(view, 1)
    host.resize(*size)
    host.show()
    qt_app.processEvents()
    assert host.width() == size[0]
    assert view.settings_nav.width() < view.width()
    assert view.reset_btn.isVisible()
    assert view.save_status_label.isVisible()
    assert view.reset_btn.mapTo(host, view.reset_btn.rect().bottomRight()).y() < host.height()
    output = Path(".scratch/settings-center-redesign-20261120/screenshots")
    output.mkdir(parents=True, exist_ok=True)
    for index in range(view.tabs.count()):
        view.tabs.setCurrentIndex(index)
        qt_app.processEvents()
        scrolls = view.tabs.widget(index).findChildren(QScrollArea)
        if isinstance(view.tabs.widget(index), QScrollArea):
            scrolls.append(view.tabs.widget(index))
        assert scrolls
        assert all(scroll.horizontalScrollBar().maximum() == 0 for scroll in scrolls), (index, [(s.width(), s.widget().minimumSizeHint().width(), s.horizontalScrollBar().maximum()) for s in scrolls])
        if index in (0, 1, 2, 4, 6, 7):
            host.grab().save(str(output / f"{size[0]}x{size[1]}-page-{index}.png"))
    view.focus_setting("extract_workers")
    qt_app.processEvents()
    parent = view.extract_workers_spin.parentWidget()
    while parent and not isinstance(parent, QGroupBox):
        parent = parent.parentWidget()
    assert parent.isChecked()
    assert view.extract_workers_spin.isVisible()
    view.tabs.setCurrentIndex(0)
    view.tabs.setCurrentIndex(4)
    assert parent.isChecked()
    view.focus_setting("theme")
    qt_app.processEvents()
    assert view.theme_combo.hasFocus()
    view.settings_nav.setFocus()
    seen = set()
    node = view.settings_nav
    for _ in range(2000):
        seen.add(node)
        node = node.nextInFocusChain()
        if node is view.settings_nav:
            break
    assert {view.settings_nav, view.reset_btn, view.theme_combo, view.api_key_input}.issubset(seen)
    view.prefs_controller.cancel()
    host.close()
    host.deleteLater()
    QFontDatabase.removeApplicationFont(font_id)


def test_route_change_reloads_ai_once_after_success(qt_app, service, monkeypatch):
    view = SettingsView(service)
    reloads = []
    monkeypatch.setattr(service, "reload_ai_from_settings", lambda: reloads.append(True))
    view._route_widgets["video_classification"]["model"].setText("new-vision")
    assert not reloads
    assert view.prefs_controller.flush_now()
    assert reloads == [True]
    view.font_spin.setValue(18)
    assert view.prefs_controller.flush_now()
    assert reloads == [True]


def test_settings_and_prompts_survive_new_service(qt_app, service, tmp_path):
    view = SettingsView(service)
    view.api_key_input.setText("sk-round-trip")
    view._route_widgets["video_classification"]["model"].setText("vision-round-trip")
    view.system_prompt_edit.setPlainText("重启后仍采用此角色")
    view.xmp_lang_combo.setCurrentText("en")
    assert view.prefs_controller.flush_now()
    saved = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    reopened = VideoOrganizerService(settings=saved, on_log=lambda _: None,
        db_path=str(tmp_path / "t.db"), results_json=str(tmp_path / "r.json"),
        results_csv=str(tmp_path / "c.csv"))
    fresh_view = SettingsView(reopened)
    assert fresh_view.api_key_input.text() == "sk-round-trip"
    assert fresh_view._route_widgets["video_classification"]["model"].text() == "vision-round-trip"
    assert fresh_view.system_prompt_edit.toPlainText() == "重启后仍采用此角色"
    assert fresh_view.xmp_lang_combo.currentText() == "en"
    fresh_view.prefs_controller.cancel()
    fresh_view.deleteLater()


def test_tag_config_writer_returns_failure_for_real_io_error(service, monkeypatch):
    def fail(*args):
        raise OSError("private-sensitive-error")
    monkeypatch.setattr("core.video_organizer_service.write_json_atomic", fail)
    assert service.save_tag_config(copy.deepcopy(service.tag_config)) is False


def test_ordinary_edits_do_not_reconfigure_paused_monitor(qt_app, service):
    view = SettingsView(service)
    changes = []
    view.media_watch_changed.connect(lambda: changes.append(True))
    view.font_spin.setValue(18)
    assert view.prefs_controller.flush_now()
    assert not changes
    view.media_watch_page.fields["stability_seconds"].setValue(45)
    assert view.prefs_controller.flush_now()
    assert changes == [True]


def test_monitor_reconfigure_keeps_pause(service, tmp_path):
    from core.media_watch import MediaDirectoryMonitor
    from core.watch_config import normalize_watch_config
    config = normalize_watch_config({"auto_analyze": False, "directories": [{"path": str(tmp_path)}]})
    monitor = MediaDirectoryMonitor(service, config)
    monitor.pause()
    try:
        monitor.reconfigure(config)
        assert monitor._paused_all
        assert not monitor.running
    finally:
        monitor.stop(shutdown_analysis=True)


def test_http400_card_reset_refreshes_inherited_displays(qt_app, service):
    view = SettingsView(service)
    panel = view.http400_settings
    panel.global_editor.fields["request_budget"].setValue(17)
    view._suppress_provider_signals = True
    try:
        view._apply_reset_group("http400_recovery")
    finally:
        view._suppress_provider_signals = False
    assert panel.global_editor.fields["request_budget"].value() == 8
    assert panel.task_editor.fields["request_budget"].value() == 8
    assert panel.audio_editor.fields["request_budget"].value() == 8
    assert panel.task_editor.override() == {}
