# -*- coding: utf-8 -*-
"""AI / 嵌套配置持久化。"""
from pathlib import Path

import json

from core.video_organizer_service import (
    DEFAULT_SETTINGS,
    SettingsManager,
    get_settings_file_path,
    get_tag_config_file_path,
)


def test_deep_copy_defaults_not_shared():
    a = SettingsManager.deep_copy_defaults()
    SettingsManager.update_setting(a, "api.key", "K1")
    b = SettingsManager.deep_copy_defaults()
    assert b["api"]["key"] == ""
    assert DEFAULT_SETTINGS["api"]["key"] == ""
    assert a["api"]["key"] == "K1"


def test_save_and_load_api_settings(tmp_path: Path, monkeypatch):
    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(
        "core.video_organizer_service.get_settings_file_path",
        lambda: str(settings_path),
    )
    # 避免污染全局 DEFAULT
    data = SettingsManager.deep_copy_defaults()
    SettingsManager.update_setting(data, "api.key", "sk-test-persist")
    SettingsManager.update_setting(data, "api.base_url", "https://example.com/v1")
    SettingsManager.update_setting(
        data, "api.model_personalization.tag_generation", "my-tag-model"
    )
    SettingsManager.save_settings(data, db=None)

    assert settings_path.exists()
    raw = json.loads(settings_path.read_text(encoding="utf-8"))
    assert raw["api"]["key"] == "sk-test-persist"
    assert raw["api"]["base_url"] == "https://example.com/v1"
    assert raw["api"]["model_personalization"]["tag_generation"] == "my-tag-model"

    loaded = SettingsManager.load_settings(db=None)
    assert loaded["api"]["key"] == "sk-test-persist"
    assert loaded["api"]["model_personalization"]["tag_generation"] == "my-tag-model"
    # 默认值未被污染
    assert DEFAULT_SETTINGS["api"]["key"] == ""


def test_frozen_tag_config_path_is_next_to_executable(tmp_path: Path, monkeypatch):
    import core.video_organizer_service as service_module

    executable = tmp_path / "VideoOrganizer.exe"
    monkeypatch.setattr(service_module.sys, "frozen", True, raising=False)
    monkeypatch.setattr(service_module.sys, "executable", str(executable))

    assert get_tag_config_file_path() == str(tmp_path / "tag_config.json")


def test_development_tag_config_path_uses_project_root(tmp_path: Path, monkeypatch):
    import core.video_organizer_service as service_module

    monkeypatch.setattr(service_module, "_PROJECT_ROOT", str(tmp_path))
    monkeypatch.delattr(service_module.sys, "frozen", raising=False)
    monkeypatch.delattr(service_module.sys, "_MEIPASS", raising=False)

    assert get_tag_config_file_path() == str(tmp_path / "tag_config.json")


def test_reload_ai_client_uses_new_key(tmp_path: Path):
    from core.video_organizer_service import VideoOrganizerService

    svc = VideoOrganizerService(
        settings=SettingsManager.deep_copy_defaults(),
        on_log=lambda _m: None,
        db_path=str(tmp_path / "t.db"),
        results_json=str(tmp_path / "r.json"),
        results_csv=str(tmp_path / "r.csv"),
    )
    SettingsManager.update_setting(svc.settings, "api.key", "sk-new")
    SettingsManager.update_setting(svc.settings, "api.base_url", "https://new.example/v1")
    svc.reload_ai_from_settings()
    # openai client stores api_key
    assert getattr(svc.ai.client, "api_key", None) == "sk-new"