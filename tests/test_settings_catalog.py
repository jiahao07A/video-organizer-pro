# -*- coding: utf-8 -*-
"""设置目录、防抖控制器与设置页：可搜索导航 / 普通偏好自动保存 / 敏感配置明确保存。

需要 Qt，使用 offscreen 平台，不依赖真实显示器。
"""
import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from core.video_organizer_service import (  # noqa: E402
    SettingsManager,
    VideoOrganizerService,
)
from gui.models import settings_catalog as catalog  # noqa: E402
from gui.views.settings import SettingsView  # noqa: E402


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setattr("core.video_organizer_service.TAG_CONFIG_FILE", str(tmp_path / "tags.json"))
    monkeypatch.setattr("core.video_organizer_service.get_settings_file_path", lambda: str(tmp_path / "settings.json"))
    return VideoOrganizerService(
        settings=SettingsManager.deep_copy_defaults(),
        on_log=lambda _m: None,
        db_path=str(tmp_path / "t.db"),
        results_json=str(tmp_path / "r.json"),
        results_csv=str(tmp_path / "c.csv"),
    )


@pytest.fixture
def silent_boxes(monkeypatch):
    for name in ("information", "warning", "critical"):
        monkeypatch.setattr(
            QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.Ok)
        )


# —— 目录 ——

def test_catalog_categories_are_task_oriented():
    ids = [c.id for c in catalog.list_categories()]
    assert ids == ["everyday", "analysis", "ai", "prompts", "naming"]


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
    assert view.settings_search_results.count() >= 1
    assert view.focus_setting("theme") is True
    assert view.tabs.currentIndex() == 3  # 界面页签


def test_view_category_jump(qt_app, service):
    view = SettingsView(service)
    view._on_category_jump("ai")
    assert view.tabs.currentIndex() == 1


# —— 视图：普通偏好防抖自动保存 ——

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

def test_sensitive_api_key_requires_explicit_save(qt_app, service, silent_boxes, tmp_path, monkeypatch):
    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(
        "core.video_organizer_service.get_settings_file_path",
        lambda: str(settings_path),
    )
    view = SettingsView(service)
    view.api_key_input.setText("sk-sensitive")
    # 未点保存前不落盘
    assert not settings_path.exists()
    view.apply_settings()
    assert service.settings["api"]["key"] == "sk-sensitive"
    assert json.loads(settings_path.read_text(encoding="utf-8"))["api"]["key"] == "sk-sensitive"


# —— 视图：重命名入口唯一 ——

def test_rename_pattern_has_single_editable_entry(qt_app, service):
    view = SettingsView(service)
    # 导出页签仅镜像展示，不可编辑
    assert view.filename_tmpl.isReadOnly() is True
    view.pattern_input.setText("{category}-{summary}")
    assert view.filename_tmpl.text() == "{category}-{summary}"
    view.prefs_controller.flush()
    assert service.settings["rename_pattern"] == "{category}-{summary}"


# —— 视图：预览不悄悄持久化 local_prompt ——

def test_prompt_preview_does_not_persist_local_prompt(qt_app, service, tmp_path, monkeypatch):
    tag_config_path = tmp_path / "tag_config.json"
    monkeypatch.setattr(
        "core.video_organizer_service.TAG_CONFIG_FILE", str(tag_config_path)
    )
    view = SettingsView(service)
    if not view._local_prompt_edits:
        pytest.skip("无标签组可测试")
    gid = next(iter(view._local_prompt_edits))
    view._local_prompt_edits[gid].setPlainText("UNSAVED-PREVIEW-XYZ")
    view.refresh_prompt_preview()
    for g in (service.tag_config or {}).get("tag_groups") or []:
        if str(g.get("id") or "").strip() == gid:
            assert (g.get("rules") or {}).get("local_prompt") in (None, "", )
    if tag_config_path.exists():
        assert "UNSAVED-PREVIEW-XYZ" not in tag_config_path.read_text(encoding="utf-8")
