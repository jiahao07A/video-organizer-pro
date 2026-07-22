# -*- coding: utf-8 -*-
"""模型供应商档案：迁移、上限、删当前切换、resolve 写穿（S3）。"""
import pytest

from core.model_providers import (
    MAX_PROVIDERS,
    DEFAULT_DISPLAY_NAME,
    ProviderLimitError,
    ProviderError,
    migrate_legacy_api_to_providers,
    ensure_providers,
    resolve_current_provider,
    apply_provider_to_api_flat,
    add_provider,
    remove_provider,
    set_current_provider,
    update_provider,
    list_providers,
)
from core.video_organizer_service import SettingsManager


def _legacy_settings(**api_overrides):
    s = SettingsManager.deep_copy_defaults()
    # 模拟旧版：无档案
    s["model_providers"] = []
    s["current_provider_id"] = ""
    s["api"]["key"] = api_overrides.get("key", "sk-legacy")
    s["api"]["base_url"] = api_overrides.get("base_url", "https://legacy.example/v1")
    s["api"]["model_personalization"] = {
        "video_classification": api_overrides.get("cls", "model-cls"),
        "tag_generation": api_overrides.get("tag", "model-tag"),
        "content_description": api_overrides.get("desc", "model-desc"),
    }
    return s


def test_migrate_legacy_api_to_default_provider():
    s = _legacy_settings()
    migrate_legacy_api_to_providers(s)
    providers = list_providers(s)
    assert len(providers) == 1
    p = providers[0]
    assert p["display_name"] == DEFAULT_DISPLAY_NAME
    assert p["api_key"] == "sk-legacy"
    assert p["base_url"] == "https://legacy.example/v1"
    assert p["models"]["video_classification"] == "model-cls"
    assert p["models"]["tag_generation"] == "model-tag"
    assert p["models"]["content_description"] == "model-desc"
    assert s["current_provider_id"] == p["id"]


def test_migrate_skips_when_providers_exist():
    s = _legacy_settings()
    migrate_legacy_api_to_providers(s)
    first_id = s["current_provider_id"]
    s["api"]["key"] = "sk-should-not-overwrite-provider"
    migrate_legacy_api_to_providers(s)
    assert len(list_providers(s)) == 1
    assert s["current_provider_id"] == first_id
    assert list_providers(s)[0]["api_key"] == "sk-legacy"


def test_ensure_providers_writes_flat_api():
    s = _legacy_settings(key="sk-flat", tag="tag-x")
    ensure_providers(s)
    assert s["api"]["key"] == "sk-flat"
    assert s["api"]["model_personalization"]["tag_generation"] == "tag-x"
    cur = resolve_current_provider(s)
    assert cur["api_key"] == "sk-flat"


def test_add_provider_limit_five():
    s = _legacy_settings()
    ensure_providers(s)
    # 已有 1，再加 4 到满
    for i in range(MAX_PROVIDERS - 1):
        add_provider(s, display_name=f"P{i}")
    assert len(list_providers(s)) == MAX_PROVIDERS
    with pytest.raises(ProviderLimitError):
        add_provider(s, display_name="overflow")


def test_remove_current_switches_to_another():
    s = _legacy_settings()
    ensure_providers(s)
    _, p2 = add_provider(s, display_name="第二", set_as_current=True)
    assert s["current_provider_id"] == p2["id"]
    first = [p for p in list_providers(s) if p["id"] != p2["id"]][0]
    remove_provider(s, p2["id"])
    assert s["current_provider_id"] == first["id"]
    assert len(list_providers(s)) == 1


def test_cannot_remove_last_provider():
    s = _legacy_settings()
    ensure_providers(s)
    only = list_providers(s)[0]
    with pytest.raises(ProviderError):
        remove_provider(s, only["id"])
    assert len(list_providers(s)) == 1


def test_set_current_and_resolve():
    s = _legacy_settings()
    ensure_providers(s)
    _, p2 = add_provider(
        s,
        display_name="B",
        api_key="sk-b",
        base_url="https://b.example/v1",
        models={
            "video_classification": "b-cls",
            "tag_generation": "b-tag",
            "content_description": "b-desc",
        },
    )
    set_current_provider(s, p2["id"])
    cur = resolve_current_provider(s)
    assert cur["id"] == p2["id"]
    assert cur["api_key"] == "sk-b"
    assert s["api"]["key"] == "sk-b"
    assert s["api"]["base_url"] == "https://b.example/v1"
    assert s["api"]["model_personalization"]["tag_generation"] == "b-tag"


def test_update_current_provider_writes_flat():
    s = _legacy_settings()
    ensure_providers(s)
    pid = s["current_provider_id"]
    update_provider(
        s,
        pid,
        api_key="sk-updated",
        models={
            "video_classification": "u-cls",
            "tag_generation": "u-tag",
            "content_description": "u-desc",
        },
    )
    apply_provider_to_api_flat(s)
    assert s["api"]["key"] == "sk-updated"
    assert s["api"]["model_personalization"]["video_classification"] == "u-cls"


def test_load_settings_migrates_legacy(tmp_path, monkeypatch):
    """load_settings 末尾 ensure：旧 json 无档案 → 默认档案。"""
    import json

    settings_path = tmp_path / "settings.json"
    legacy = {
        "api": {
            "key": "sk-from-file",
            "base_url": "https://file.example/v1",
            "model_personalization": {
                "video_classification": "f-cls",
                "tag_generation": "f-tag",
                "content_description": "f-desc",
            },
        }
    }
    settings_path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(
        "core.video_organizer_service.get_settings_file_path",
        lambda: str(settings_path),
    )
    loaded = SettingsManager.load_settings(db=None)
    assert loaded["api"]["key"] == "sk-from-file"
    providers = loaded.get("model_providers") or []
    assert len(providers) == 1
    assert providers[0]["display_name"] == DEFAULT_DISPLAY_NAME
    assert providers[0]["api_key"] == "sk-from-file"
    assert loaded["current_provider_id"] == providers[0]["id"]


def test_reload_ai_uses_current_provider_key(tmp_path):
    from core.video_organizer_service import VideoOrganizerService

    s = SettingsManager.deep_copy_defaults()
    ensure_providers(s)
    pid = s["current_provider_id"]
    update_provider(s, pid, api_key="sk-provider", base_url="https://p.example/v1")
    apply_provider_to_api_flat(s)

    svc = VideoOrganizerService(
        settings=s,
        on_log=lambda _m: None,
        db_path=str(tmp_path / "t.db"),
        results_json=str(tmp_path / "r.json"),
        results_csv=str(tmp_path / "r.csv"),
    )
    # 切换到新档案
    _, p2 = add_provider(
        svc.settings,
        display_name="Alt",
        api_key="sk-alt",
        base_url="https://alt.example/v1",
        set_as_current=True,
    )
    assert p2["api_key"] == "sk-alt"
    svc.reload_ai_from_settings()
    assert getattr(svc.ai.client, "api_key", None) == "sk-alt"