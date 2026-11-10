# -*- coding: utf-8 -*-
"""AI / 嵌套配置持久化。"""
import json
import sqlite3
import pytest
from pathlib import Path

from core.video_organizer_service import (
    DEFAULT_SETTINGS,
    SettingsManager,
    VideoOrganizerService,
    get_tag_config_file_path,
)
from core.data_directory import LegacyLayoutDetected


def test_file_edits_survive_database_conflict_save_and_restart(tmp_path):
    from core.video_organizer_service import DatabaseManager

    db = DatabaseManager(str(tmp_path / 'test.db'))
    original = SettingsManager.deep_copy_defaults()
    SettingsManager.save_settings(original, db)
    path = tmp_path / 'settings.json'
    edited = json.loads(path.read_text(encoding='utf-8'))
    edited['ui_preferences']['font_size'] = 19
    edited['custom_user_options'] = {'note': '手工修改请保留'}
    edited['model_providers'][0]['api_key'] = 'edited-placeholder'
    edited['task_model_routing']['video_classification']['model'] = 'manual-model'
    path.write_text(json.dumps(edited), encoding='utf-8')

    loaded = SettingsManager.load_settings(db)
    assert loaded['ui_preferences']['font_size'] == 19
    assert loaded['api']['key'] == 'edited-placeholder'
    assert loaded['task_model_routing']['video_classification']['model'] == 'manual-model'
    SettingsManager.save_settings(loaded, db)
    reloaded = SettingsManager.load_settings(db)
    assert reloaded['ui_preferences']['font_size'] == 19
    assert reloaded['custom_user_options'] == {'note': '手工修改请保留'}


@pytest.mark.parametrize('content', ['{"unfinished":', '[]', 'null'])
def test_invalid_settings_are_rejected_without_overwriting(tmp_path, content):
    path = tmp_path / 'settings.json'
    path.write_text(content, encoding='utf-8')
    with pytest.raises(ValueError, match='配置文件'):
        SettingsManager.load_settings(settings_path=str(path))
    assert path.read_text(encoding='utf-8') == content


def test_windows_utf8_bom_settings_are_read(tmp_path):
    path = tmp_path / 'settings.json'
    path.write_text(json.dumps({'rename_pattern': '手工命名-{original_name}'}), encoding='utf-8-sig')
    loaded = SettingsManager.load_settings(settings_path=str(path))
    assert loaded['rename_pattern'] == '手工命名-{original_name}'


def test_legacy_database_settings_require_explicit_recovery(tmp_path):
    from core.video_organizer_service import DatabaseManager

    db = DatabaseManager(str(tmp_path / 'test.db'))
    db.save_setting('settings_blob', {'rename_pattern': 'legacy-pattern'})
    assert SettingsManager.load_settings(db)['rename_pattern'] == DEFAULT_SETTINGS['rename_pattern']
    recovered = SettingsManager.load_settings(db, recover_from_database=True)
    assert recovered['rename_pattern'] == 'legacy-pattern'
    path = tmp_path / 'settings.json'
    path.write_text(json.dumps({'rename_pattern': 'file-pattern'}), encoding='utf-8')
    assert SettingsManager.load_settings(db, recover_from_database=True)['rename_pattern'] == 'file-pattern'


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

    assert get_tag_config_file_path() == str(tmp_path / "data" / "tag_config.json")


def test_development_tag_config_path_uses_application_data_root(tmp_path: Path, monkeypatch):
    import core.video_organizer_service as service_module

    monkeypatch.setattr(service_module, "_PROJECT_ROOT", str(tmp_path))
    monkeypatch.delattr(service_module.sys, "frozen", raising=False)
    monkeypatch.delattr(service_module.sys, "_MEIPASS", raising=False)

    assert get_tag_config_file_path() == str(tmp_path / "data" / "tag_config.json")


def test_reload_ai_client_uses_new_key(tmp_path: Path):
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


def test_explicit_service_paths_are_isolated_from_default_legacy_layout(tmp_path: Path):
    svc = VideoOrganizerService(
        settings=SettingsManager.deep_copy_defaults(),
        on_log=lambda _m: None,
        db_path=str(tmp_path / "library.db"),
        results_json=str(tmp_path / "results.json"),
        results_csv=str(tmp_path / "results.csv"),
    )

    assert svc._settings_path == str(tmp_path / "settings.json")
    assert svc._tag_config_path == str(tmp_path / "tag_config.json")
    assert svc._backup_dir == str(tmp_path / "backups")
    assert svc._thumbnail_dir == str(tmp_path / ".thumbnails")
    assert svc.file_manager.json_path == str(tmp_path / "results.json")

    SettingsManager.save_settings(svc.settings, svc.db)
    svc.save_tag_config({"version": "6.0", "tag_groups": []})
    backup_path = svc.backup_configuration()

    assert (tmp_path / "settings.json").exists()
    assert (tmp_path / "tag_config.json").exists()
    assert Path(backup_path).parent == tmp_path / "backups"


def test_explicit_service_migrates_its_custom_database_and_thumbnail_paths(
    tmp_path: Path,
):
    from core.data_directory import DataDirectoryManager

    source = tmp_path / "custom-store"
    source.mkdir()
    thumbnail = source / ".thumbnails" / "thumb.webp"
    thumbnail.parent.mkdir()
    thumbnail.write_bytes(b"thumbnail")
    service = VideoOrganizerService(
        settings=SettingsManager.deep_copy_defaults(),
        on_log=lambda _message: None,
        db_path=str(source / "library.sqlite"),
        results_json=str(source / "custom-results.json"),
        results_csv=str(source / "custom-results.csv"),
    )
    service.db.upsert_video(
        {
            "path": str(tmp_path / "clip.mp4"),
            "filename": "clip.mp4",
            "status": "pending",
            "thumbnail": str(thumbnail),
        }
    )
    stored = service.db.get_video_by_path(str(tmp_path / "clip.mp4"))
    assert stored["thumbnail"] == ".thumbnails/thumb.webp"

    target = tmp_path / "migrated-store"
    paths = service.migrate_data_directory(str(target))

    assert Path(paths.database).exists()
    assert Path(paths.thumbnails, "thumb.webp").read_bytes() == b"thumbnail"
    connection = sqlite3.connect(paths.database)
    try:
        assert connection.execute(
            "SELECT path, thumbnail FROM videos"
        ).fetchone() == (str(tmp_path / "clip.mp4"), ".thumbnails/thumb.webp")
    finally:
        connection.close()
    assert Path(source, "library.sqlite").exists()
    assert list(Path(paths.backups).glob("backup_*"))
    assert DataDirectoryManager(application_directory=str(tmp_path)).read_locator() == str(
        target.resolve()
    )


def test_default_service_path_still_blocks_legacy_layout(
    tmp_path: Path, monkeypatch
):
    import core.video_organizer_service as service_module

    monkeypatch.setattr(service_module, "_PROJECT_ROOT", str(tmp_path))
    monkeypatch.delattr(service_module.sys, "frozen", raising=False)
    (tmp_path / "video_organizer.db").write_bytes(b"legacy")

    try:
        VideoOrganizerService()
    except LegacyLayoutDetected as exc:
        assert "video_organizer.db" in str(exc)
    else:
        raise AssertionError("默认服务路径未阻止程序旁旧布局")


def test_explicit_service_reports_its_data_directory(tmp_path: Path):
    svc = VideoOrganizerService(
        settings=SettingsManager.deep_copy_defaults(),
        on_log=lambda _m: None,
        db_path=str(tmp_path / "library.db"),
        results_json=str(tmp_path / "results.json"),
        results_csv=str(tmp_path / "results.csv"),
    )

    inspection = svc.data_directory_inspection()

    assert inspection.state.value == "active"
    assert inspection.active_root == str(tmp_path)
