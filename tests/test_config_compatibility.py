"""仅使用临时合成配置，覆盖升级、无损继承和失败恢复。"""
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from core import config_compatibility as compat
from core.model_providers import (
    ensure_providers, replace_providers, replace_task_model_routing,
    resolve_task_route, update_provider,
)
from core.video_organizer_service import DEFAULT_SETTINGS, DatabaseManager, SettingsManager, VideoOrganizerService


def write_config(path, payload, encoding="utf-8"):
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding=encoding)
    return path.read_bytes()


def settings_adapter(raw):
    return compat.adapt_settings(raw, DEFAULT_SETTINGS)


@pytest.fixture
def tag_service(tmp_path):
    service = VideoOrganizerService.__new__(VideoOrganizerService)
    service._tag_config_path = str(tmp_path / "tag_config.json")
    service.db = DatabaseManager(str(tmp_path / "test.db"))
    service.ai = SimpleNamespace(tag_config={})
    service.tag_config = {}
    return service


def test_settings_upgrade_preserves_legacy_key_extensions_and_source_bytes(tmp_path):
    path = tmp_path / "settings.json"
    source = write_config(path, {
        "api": {"key": "synthetic-secret", "base_url": "https://example.test/v1",
                "model_personalization": {"tag_generation": "custom-model", "extension": {"keep": True}}},
        "processing": {"max_workers": "3", "enable_audio_transcription": "false"},
        "custom": {"blank": [], "disabled": False, "zero": 0},
    }, "utf-8-sig")
    loaded = SettingsManager.load_settings(settings_path=str(path))
    assert loaded[compat.SCHEMA_KEY] == 1
    assert loaded["model_providers"][0]["api_key"] == "synthetic-secret"
    assert loaded["model_providers"][0]["models"]["extension"] == {"keep": True}
    assert loaded["task_model_routing"]["tag_generation"]["model"] == "custom-model"
    assert loaded["processing"]["max_workers"] == 3
    assert loaded["processing"]["enable_audio_transcription"] is False
    assert loaded["custom"] == {"blank": [], "disabled": False, "zero": 0}
    assert next((tmp_path / "backups/config-upgrades").glob("*.bak")).read_bytes() == source
    stamp = path.stat().st_mtime_ns
    assert SettingsManager.load_settings(settings_path=str(path)) == loaded
    assert path.stat().st_mtime_ns == stamp
    assert len(list((tmp_path / "backups/config-upgrades").glob("*.bak"))) == 1


def test_explicit_model_inheritance_survives_load_save_and_default_change(tmp_path):
    path = tmp_path / "settings.json"
    raw = {"model_providers": [
        {"id": "first", "models": {"tag_generation": "first-model"}, "extension": [1]},
        {"id": "second", "api_key": "secret", "models": {"tag_generation": "second-model", "x": [2]}},
    ], "current_provider_id": "first", "task_model_routing": {
        "tag_generation": {"provider_id": "second", "model": "", "extension": {"a": 1}},
        "future_task": ["opaque"],
    }}
    write_config(path, raw)
    loaded = SettingsManager.load_settings(settings_path=str(path))
    assert loaded["task_model_routing"]["tag_generation"]["model"] == ""
    assert resolve_task_route(loaded, "tag_generation")["model"] == "second-model"
    update_provider(loaded, "second", models={"tag_generation": "updated-model"})
    replace_task_model_routing(loaded, {"tag_generation": {"provider_id": "second", "model": ""}})
    SettingsManager.save_settings(loaded, settings_path=str(path))
    loaded = SettingsManager.load_settings(settings_path=str(path))
    assert resolve_task_route(loaded, "tag_generation")["model"] == "updated-model"
    assert loaded["task_model_routing"]["future_task"] == ["opaque"]
    assert loaded["task_model_routing"]["tag_generation"]["extension"] == {"a": 1}
    assert loaded["model_providers"][1]["models"]["x"] == [2]
    replace_providers(loaded, [{"id": "first", "display_name": "renamed"}, loaded["model_providers"][1]], "first")
    assert loaded["model_providers"][0]["extension"] == [1]
    assert loaded["model_providers"][1]["api_key"] == "secret"


def test_watch_upgrade_preserves_identity_disabled_overrides_and_unknowns(tmp_path):
    path = tmp_path / "settings.json"
    raw = {"processing": {"media_watch": {
        "recursive": "false", "extensions": ".mp4; .mov", "include_patterns": [], "x": [1],
        "directories": [{"id": "stable-id", "path": str(tmp_path / "clips"), "name": "片库",
                         "enabled": "false", "recursive": None, "request_interval": "0",
                         "include_patterns": [], "notify_failure": False, "x": {"a": 2}}],
    }}}
    write_config(path, raw)
    loaded = SettingsManager.load_settings(settings_path=str(path))
    watch = loaded["processing"]["media_watch"]
    assert watch["recursive"] is False
    assert watch["extensions"] == [".mp4", ".mov"]
    assert watch["x"] == [1]
    directory = watch["directories"][0]
    assert directory["id"] == "stable-id" and directory["name"] == "片库"
    assert directory["enabled"] is False
    assert directory["recursive"] is None
    assert directory["request_interval"] == 0
    assert directory["include_patterns"] == [] and directory["notify_failure"] is False
    assert directory["x"] == {"a": 2}
    assert settings_adapter(loaded) == loaded


def test_legacy_tag_upgrade_preserves_prompts_exports_categories_and_extensions(tag_service):
    path = Path(tag_service._tag_config_path)
    raw = {"version": "5.0", "config": [{"id": "mood", "display_name": "情绪", "exclusive": "false",
           "ai_expandable": "true", "max_count": "3", "description": "组提示词", "tags": ["快乐"], "x": [1]}],
           "global_settings": {"system_prompt": "自定义全局提示词", "export_schemes": {"ale_columns": [], "x": 2}},
           "categories": [], "x": {"extra": False}}
    source = write_config(path, raw, "utf-8-sig")
    loaded = tag_service.load_tag_config()
    mood = next(g for g in loaded["tag_groups"] if g["id"] == "mood")
    assert mood["tags"] == ["快乐"] and mood["x"] == [1]
    assert mood["rules"] == {"selection_mode": "multiple", "ai_expandable": True, "max_count": 3, "local_prompt": "组提示词"}
    assert loaded["global_settings"]["system_prompt"] == "自定义全局提示词"
    assert loaded["global_settings"]["export_schemes"]["ale_columns"] == []
    assert loaded["global_settings"]["export_schemes"]["x"] == 2
    assert loaded["categories"] == [] and loaded["x"] == {"extra": False}
    assert next((path.parent / "backups/config-upgrades").glob("*.bak")).read_bytes() == source
    before = path.stat().st_mtime_ns
    assert tag_service.load_tag_config() == loaded
    assert path.stat().st_mtime_ns == before


def test_empty_modern_groups_do_not_resurrect_stale_legacy_tags(tmp_path):
    path = tmp_path / "tag_config.json"
    write_config(path, {"tag_groups": [], "config": [{"id": "mood", "tags": ["旧词不应复活"]}]})
    loaded = compat.load_config(str(path), compat.adapt_tag_config)
    assert "旧词不应复活" not in next(g for g in loaded["tag_groups"] if g["id"] == "mood")["tags"]


@pytest.mark.parametrize("content", ["{\"key\":\"secret\",", "[]", "null", "{\"global_settings\":null}",
                                       '{"tag_groups":{}}', '{"tag_groups":[{"id":"mood","tags":null}]}'])
def test_invalid_tag_file_stops_load_without_default_fallback(tag_service, content):
    path = Path(tag_service._tag_config_path)
    path.write_text(content, encoding="utf-8")
    with pytest.raises(compat.ConfigurationError) as error:
        tag_service.load_tag_config()
    assert "secret" not in str(error.value)
    assert path.read_text(encoding="utf-8") == content
    assert not (path.parent / "backups/config-upgrades").exists()


@pytest.mark.parametrize("payload", [{compat.SCHEMA_KEY: 2, "api": {"key": "secret"}},
                                     {compat.SCHEMA_KEY: True}, {compat.SCHEMA_KEY: "1"},
                                     {"processing": None}, {"model_providers": [None]},
                                     {"model_providers": [{"models": None}]},
                                     {"task_model_routing": {"tag_generation": None}},
                                     {"processing": {"media_watch": {"directories": {}}}},
                                     {"processing": {"media_watch": {"max_workers": "nan"}}},
                                     {"processing": {"max_workers": "secret"}}])
def test_unsafe_settings_reject_load_and_save_without_source_change(tmp_path, payload):
    path = tmp_path / "settings.json"
    source = write_config(path, payload)
    for action in (lambda: SettingsManager.load_settings(settings_path=str(path)),
                   lambda: SettingsManager.save_settings({}, settings_path=str(path)),
                   lambda: SettingsManager.save_settings(payload, settings_path=str(path))):
        with pytest.raises(compat.ConfigurationError) as error:
            action()
        assert "secret" not in str(error.value)
        assert path.read_bytes() == source


@pytest.mark.parametrize("payload", [{"version": "10.0"}, {"version": "6.1"},
                                     {"version": "unknown-secret"}, {compat.SCHEMA_KEY: 2},
                                     {"version": "6.0", "global_settings": None}])
def test_future_or_invalid_tag_config_rejects_load_and_both_save_paths(tag_service, payload, caplog):
    path = Path(tag_service._tag_config_path)
    source = write_config(path, payload)
    with pytest.raises(compat.ConfigurationError):
        tag_service.load_tag_config()
    assert tag_service.save_tag_config({"version": "6.0"}) is False
    assert path.read_bytes() == source
    path.unlink()  # 明确单文件，验证内存的未来配置同样不能保存。
    assert tag_service.save_tag_config(payload) is False
    assert not path.exists()
    assert "secret" not in caplog.text


@pytest.mark.parametrize("failing_stage", ["backup", "write"])
def test_upgrade_failure_preserves_source_and_retries_idempotently(tmp_path, monkeypatch, failing_stage):
    path = tmp_path / "settings.json"
    source = write_config(path, {"rename_pattern": "保留"}, "utf-8-sig")
    function = "_backup_source" if failing_stage == "backup" else "write_json_atomic"
    original = getattr(compat, function)

    def fail(*args):
        raise OSError("synthetic-secret")

    monkeypatch.setattr(compat, function, fail)
    for _ in range(2):
        with pytest.raises(compat.ConfigurationError) as error:
            SettingsManager.load_settings(settings_path=str(path))
        assert "synthetic-secret" not in str(error.value)
        assert path.read_bytes() == source
    monkeypatch.setattr(compat, function, original)
    assert SettingsManager.load_settings(settings_path=str(path))["rename_pattern"] == "保留"
    backups = list((tmp_path / "backups/config-upgrades").glob("*.bak"))
    assert len(backups) == 1 and backups[0].read_bytes() == source


def test_upgrade_detects_changed_source_before_replacing(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    source = write_config(path, {"rename_pattern": "before"})
    backup = compat._backup_source

    def concurrent_edit(file, content):
        result = backup(file, content)
        write_config(path, {"rename_pattern": "edited-elsewhere"})
        return result

    monkeypatch.setattr(compat, "_backup_source", concurrent_edit)
    with pytest.raises(compat.ConfigurationError, match="其他程序"):
        SettingsManager.load_settings(settings_path=str(path))
    assert json.loads(path.read_bytes())["rename_pattern"] == "edited-elsewhere"
    assert next((tmp_path / "backups/config-upgrades").glob("*.bak")).read_bytes() == source


def test_missing_files_do_not_create_config_or_upgrade_backups(tmp_path, tag_service):
    assert SettingsManager.load_settings(settings_path=str(tmp_path / "settings.json"))[compat.SCHEMA_KEY] == 1
    assert tag_service.load_tag_config()[compat.SCHEMA_KEY] == 1
    assert not (tmp_path / "settings.json").exists()
    assert not Path(tag_service._tag_config_path).exists()
    assert not (tmp_path / "backups").exists()


def test_upgrade_of_one_file_does_not_mask_failure_of_other_file(tmp_path):
    settings_path = tmp_path / "settings.json"
    tag_path = tmp_path / "tag_config.json"
    write_config(settings_path, {"rename_pattern": "keep"})
    source = write_config(tag_path, {"version": "10.0"})
    assert SettingsManager.load_settings(settings_path=str(settings_path))[compat.SCHEMA_KEY] == 1
    with pytest.raises(compat.ConfigurationError):
        compat.load_config(str(tag_path), compat.adapt_tag_config)
    assert tag_path.read_bytes() == source


def test_pending_projection_cannot_overwrite_future_tag_file(tag_service):
    path = Path(tag_service._tag_config_path)
    source = write_config(path, {"version": "10.0", "marker": "future"})
    pending = {"config": {"version": "6.0", "tag_groups": []}, "backup": "synthetic-backup"}
    tag_service.db.set_storage_metadata("tag_import_projection_pending", pending)
    with pytest.raises(compat.ConfigurationError):
        tag_service.load_tag_config()
    assert path.read_bytes() == source
    assert tag_service.db.get_storage_metadata("tag_import_projection_pending") == pending


def test_pending_projection_keeps_existing_corruption_recovery_protocol(tag_service):
    path = Path(tag_service._tag_config_path)
    path.write_bytes(b'{"broken":')
    tag_service.db.set_storage_metadata("tag_import_projection_pending", {
        "config": {"version": "6.0", "tag_groups": [], "marker": "recovered"}, "backup": "synthetic-backup"})
    assert tag_service.load_tag_config()["marker"] == "recovered"
    assert json.loads(path.read_bytes())[compat.SCHEMA_KEY] == 1
    assert tag_service.db.get_storage_metadata("tag_import_projection_pending") is None
    assert next((path.parent / "backups/config-upgrades").glob("*.bak")).read_bytes() == b'{"broken":'


def test_compatibility_adapters_do_not_mutate_inputs():
    raw = {"api": {"key": "synthetic"}, "processing": {"max_workers": "4"}}
    original = copy.deepcopy(raw)
    settings_adapter(raw)
    assert raw == original
    tag = {"config": [{"id": "mood", "exclusive": "false", "tags": []}]}
    original = copy.deepcopy(tag)
    compat.adapt_tag_config(tag)
    assert tag == original


def test_existing_provider_count_above_limit_is_not_silently_truncated(tmp_path):
    path = tmp_path / "settings.json"
    providers = [{"id": f"p{i}", "api_key": f"synthetic-{i}"} for i in range(6)]
    write_config(path, {"model_providers": providers})
    loaded = SettingsManager.load_settings(settings_path=str(path))
    ensure_providers(loaded)
    assert [p["api_key"] for p in loaded["model_providers"]] == [p["api_key"] for p in providers]


def test_locator_bom_compatibility_keeps_missing_database_gate(tmp_path):
    from core.data_directory import DataDirectoryManager, DataDirectoryState

    application = tmp_path / "app"
    application.mkdir()
    manager = DataDirectoryManager(application_directory=str(application))
    root = tmp_path / "selected"
    root.mkdir()
    locator = application / ".video-organizer-data.json"
    original = write_config(locator, {"format": "video-organizer-data-root", "version": 1,
                                     "data_root": str(root), "database_initialized": True,
                                     "updated_at": "2026-11-21T00:00:00"}, "utf-8-sig")
    assert manager.read_locator() == str(root)
    assert manager.inspect().state is DataDirectoryState.INVALID
    assert locator.read_bytes() == original


def test_tag_save_failure_keeps_edits_and_retry_recovers(tag_service, monkeypatch, caplog):
    from core import video_organizer_service as service_module

    path = Path(tag_service._tag_config_path)
    write_config(path, {"version": "6.0", "global_settings": {"system_prompt": "before"}})
    tag_service.tag_config = tag_service.load_tag_config()
    edited = copy.deepcopy(tag_service.tag_config)
    edited["global_settings"]["system_prompt"] = "edited"
    source = path.read_bytes()
    original = service_module.write_json_atomic

    def fail(*args):
        raise OSError("synthetic-secret")

    monkeypatch.setattr(service_module, "write_json_atomic", fail)
    assert tag_service.save_tag_config(edited) is False
    assert tag_service.tag_config["global_settings"]["system_prompt"] == "edited"
    assert path.read_bytes() == source
    assert "synthetic-secret" not in caplog.text
    monkeypatch.setattr(service_module, "write_json_atomic", original)
    assert tag_service.save_tag_config(edited) is True
    assert tag_service.load_tag_config()["global_settings"]["system_prompt"] == "edited"


def test_invalid_duplicate_identities_are_not_silently_merged(tmp_path):
    path = tmp_path / "settings.json"
    for raw in ({"model_providers": [{"id": "same"}, {"id": " same "}]},
                {"processing": {"media_watch": {"directories": [str(tmp_path), {"path": str(tmp_path)}]}}}):
        source = write_config(path, raw)
        with pytest.raises(compat.ConfigurationError):
            SettingsManager.load_settings(settings_path=str(path))
        assert path.read_bytes() == source


@pytest.mark.parametrize("stage", ["backup", "configuration"])
def test_atomic_replace_failure_keeps_original_and_no_partial_snapshot(tmp_path, monkeypatch, stage):
    path = tmp_path / "settings.json"
    source = write_config(path, {"rename_pattern": "source"})
    original = compat.os.replace

    def fail_selected_replace(source_path, destination):
        destination = Path(destination)
        if (stage == "backup" and destination.suffix == ".bak") or (stage == "configuration" and destination == path):
            raise PermissionError("synthetic-secret")
        return original(source_path, destination)

    monkeypatch.setattr(compat.os, "replace", fail_selected_replace)
    with pytest.raises(compat.ConfigurationError):
        SettingsManager.load_settings(settings_path=str(path))
    assert path.read_bytes() == source
    folder = tmp_path / "backups/config-upgrades"
    backups = list(folder.glob("*.bak"))
    assert len(backups) == (0 if stage == "backup" else 1)
    assert not list(folder.glob("*.tmp"))
    assert not list(tmp_path.glob("*.tmp"))
    monkeypatch.setattr(compat.os, "replace", original)
    assert SettingsManager.load_settings(settings_path=str(path))["rename_pattern"] == "source"
    assert next(folder.glob("*.bak")).read_bytes() == source


def test_validation_traceback_does_not_echo_invalid_field_value():
    import traceback

    payload = {"processing": {"max_workers": "synthetic-secret"}}
    try:
        settings_adapter(payload)
    except compat.ConfigurationError:
        assert "synthetic-secret" not in traceback.format_exc()
    else:
        pytest.fail("invalid numeric value must stop adaptation")
