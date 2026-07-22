# -*- coding: utf-8 -*-
"""冷启动 AI 分簇：解析与服务门面（可注入）。"""
from pathlib import Path

import pytest

from core.tag_ai_assist import parse_ai_cold_start_clusters, rule_cold_start_cluster
from core.video_organizer_service import DEFAULT_SETTINGS, VideoOrganizerService


def test_parse_ai_cold_start_rejects_invented_words():
    data = {
        "clusters": [
            {"standard": "男性", "aliases": ["男", "发明词"], "group_id": "subject"},
            {"standard": "赛博朋克", "aliases": [], "group_id": "mood"},  # 不在 allowed
        ]
    }
    clusters = parse_ai_cold_start_clusters(data, ["男性", "男", "办公"])
    standards = {c.standard for c in clusters}
    assert "男性" in standards
    assert "赛博朋克" not in standards
    male = next(c for c in clusters if c.standard == "男性")
    assert "男" in male.aliases
    assert "发明词" not in male.aliases
    # 漏网词补全
    assert "办公" in standards


def test_rule_cold_start_assigns_group():
    clusters = rule_cold_start_cluster(["治愈", "男性"])
    by = {c.standard: c.group_id for c in clusters}
    assert by.get("治愈") == "mood"
    assert by.get("男性") == "subject"


def test_cold_start_ai_paused_by_default(tmp_path: Path, monkeypatch):
    """默认直接加载词表，不调用 AI。"""
    from core.tag_ai_assist import COLD_START_AI_ENABLED

    assert COLD_START_AI_ENABLED is False
    svc = VideoOrganizerService(
        settings={**DEFAULT_SETTINGS},
        on_log=lambda _m: None,
        db_path=str(tmp_path / "t.db"),
        results_json=str(tmp_path / "r.json"),
        results_csv=str(tmp_path / "r.csv"),
    )
    called = {"n": 0}

    def boom(*a, **k):
        called["n"] += 1
        raise AssertionError("default path must not call AI API")

    monkeypatch.setattr(svc.ai, "_get_api_response", boom)
    draft = svc.build_cold_start_draft(
        ["# ========== 氛围 mood ==========", "开心 ← 快乐"]
    )
    assert draft is not None
    assert called["n"] == 0
    assert draft.alias_map.get("快乐") == "开心"
    assert any("直接加载" in n for n in (draft.notes or []))


def test_service_cold_start_fallback_on_api_fail(tmp_path: Path, monkeypatch):
    svc = VideoOrganizerService(
        settings={**DEFAULT_SETTINGS},
        on_log=lambda _m: None,
        db_path=str(tmp_path / "t.db"),
        results_json=str(tmp_path / "r.json"),
        results_csv=str(tmp_path / "r.csv"),
    )
    monkeypatch.setattr(svc.ai, "_get_api_response", lambda *a, **k: None)
    draft = svc.build_cold_start_draft(["治愈", "开心"], use_ai=True)
    assert draft is not None
    assert any("降级" in n or "规则" in n for n in (draft.notes or []))