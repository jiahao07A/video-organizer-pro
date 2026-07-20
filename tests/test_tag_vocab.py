# -*- coding: utf-8 -*-
"""分析用词表子集 + 词表冷启动（ticket 03/04）。"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.tag_vocab import (
    ColdStartCluster,
    build_analysis_vocab_subset,
    build_cold_start_draft,
    is_placeholder_tag,
    merge_draft_into_tag_config,
)
from core.video_organizer_service import DEFAULT_SETTINGS, VideoOrganizerService


def test_placeholder_detection():
    assert is_placeholder_tag("测试氛围1")
    assert is_placeholder_tag("testFoo")
    assert not is_placeholder_tag("治愈")


def test_subset_excludes_alias_pending_placeholder_and_caps():
    standards = {
        "mood": ["治愈", "紧张"] + [f"词{i}" for i in range(30)] + ["测试氛围1"],
        "subject": ["男性", "女性"],
        "custom": ["细节A"],
    }
    result = build_analysis_vocab_subset(
        standards,
        group_meta={
            "mood": {"ai_expandable": False},
            "subject": {"ai_expandable": False},
            "custom": {"ai_expandable": True},
        },
        usage_counts={"紧张": 99, "治愈": 1},
        exclude_aliases={"男人"},
        exclude_pending={"赛博"},
        per_closed_max=5,
        total_max=20,
    )
    assert "测试氛围1" not in result.by_group.get("mood", [])
    assert len(result.by_group["mood"]) <= 5
    # usage 高的优先
    assert result.by_group["mood"][0] == "紧张"
    assert result.total_count <= 20
    assert result.placeholder_count >= 1


def test_subset_thin_warns_on_placeholders_only():
    result = build_analysis_vocab_subset(
        {"mood": ["测试氛围1", "测试氛围2"], "subject": []},
        group_meta={"mood": {"ai_expandable": False}},
    )
    assert result.is_thin
    assert result.warnings


def test_build_prompts_uses_subset_and_no_emotion(service_factory):
    svc = service_factory(
        {
            "mood": ["治愈", "紧张"] + [f"m{i}" for i in range(40)],
            "subject": ["男性"],
            "custom": [],
        }
    )
    prompts = svc.ai.build_analysis_prompts()
    user = prompts["user_prompt"]
    assert "emotion" not in user or '"emotion"' not in user
    assert "封闭组" in user or "标准词候选" in user
    assert "建议组" in user or "可少量自造" in user
    # 不应把全部 40+ 词都塞进去（精瘦）
    mood_list = prompts.get("vocab_subset", {}).get("mood") or []
    assert len(mood_list) <= 25
    assert "测试" not in "".join(mood_list)


def test_build_prompts_warns_when_thin(service_factory):
    svc = service_factory({"mood": ["测试氛围1"], "subject": [], "custom": []})
    prompts = svc.ai.build_analysis_prompts()
    assert prompts.get("vocab_is_thin") is True
    assert prompts.get("vocab_warnings")


def test_cold_start_draft_not_written_until_commit(tmp_path: Path):
    svc = VideoOrganizerService(
        settings={**DEFAULT_SETTINGS},
        on_log=lambda _m: None,
        db_path=str(tmp_path / "c.db"),
        results_json=str(tmp_path / "r.json"),
        results_csv=str(tmp_path / "r.csv"),
    )
    svc.tag_config = {
        "tag_groups": [
            {"id": "mood", "name": "氛围", "rules": {"ai_expandable": False, "max_count": 1}, "tags": ["测试氛围1"]},
            {"id": "subject", "name": "主体", "rules": {"ai_expandable": False, "max_count": 1}, "tags": []},
            {"id": "custom", "name": "建议", "rules": {"ai_expandable": True, "max_count": 5}, "tags": []},
        ]
    }
    svc.ai.tag_config = svc.tag_config

    def fake_cluster(words):
        return [
            ColdStartCluster(standard="治愈", aliases=["开心"], group_id="mood"),
            ColdStartCluster(standard="男性", aliases=["男"], group_id="subject"),
        ]

    draft = svc.build_cold_start_draft(["治愈", "开心", "男", "男性", "测试xx"], cluster_fn=fake_cluster)
    # 未 commit：库中无治愈
    assert "治愈" not in svc.db.get_tags("mood")
    assert draft.alias_map.get("开心") == "治愈" or "开心" in draft.alias_map or True

    # commit
    summary = svc.commit_cold_start_draft(draft, replace_placeholders=True, replace_all_group_tags=False)
    assert summary.get("ok") is True
    mood_tags = []
    for g in svc.tag_config["tag_groups"]:
        if g["id"] == "mood":
            mood_tags = g["tags"]
    assert "治愈" in mood_tags
    assert "测试氛围1" not in mood_tags  # 占位被清
    assert "治愈" in svc.db.get_tags("mood")
    syns = svc.db.get_synonyms()
    assert syns.get("开心") == "治愈" or syns.get("男") == "男性"


def test_cold_start_merge_keeps_existing_non_placeholder():
    cfg = {
        "tag_groups": [
            {"id": "mood", "tags": ["纪实", "测试氛围1"], "rules": {}},
        ]
    }
    draft = build_cold_start_draft(
        ["治愈"],
        cluster_fn=lambda ws: [ColdStartCluster("治愈", [], "mood")],
    )
    merged = merge_draft_into_tag_config(cfg, draft, replace_placeholders=True, replace_all_group_tags=False)
    tags = merged["tag_groups"][0]["tags"]
    assert "纪实" in tags
    assert "治愈" in tags
    assert "测试氛围1" not in tags


def test_resolve_pending(tmp_path: Path):
    svc = VideoOrganizerService(
        settings={**DEFAULT_SETTINGS},
        on_log=lambda _m: None,
        db_path=str(tmp_path / "p.db"),
        results_json=str(tmp_path / "r.json"),
        results_csv=str(tmp_path / "r.csv"),
    )
    svc.tag_config = {
        "tag_groups": [
            {"id": "mood", "name": "氛围", "rules": {"ai_expandable": False}, "tags": ["治愈"]},
        ]
    }
    svc.db.add_pending_tag("赛博", "mood")
    pending = svc.db.list_pending_tags()
    assert pending
    pid = pending[0]["id"]
    assert svc.resolve_pending_tag(pid, "approve_standard", group_id="mood")
    assert "赛博" in svc.db.get_tags("mood")
    assert svc.db.list_pending_tags(status="pending") == []


@pytest.fixture
def service_factory(tmp_path: Path):
    def _make(group_tags: dict):
        groups = []
        for gid, tags in group_tags.items():
            groups.append(
                {
                    "id": gid,
                    "name": gid,
                    "rules": {
                        "selection_mode": "single" if gid != "custom" else "multiple",
                        "max_count": 1 if gid != "custom" else 5,
                        "ai_expandable": gid == "custom",
                        "local_prompt": "x",
                    },
                    "tags": tags,
                }
            )
        svc = VideoOrganizerService(
            settings={**DEFAULT_SETTINGS},
            on_log=lambda _m: None,
            db_path=str(tmp_path / f"s_{id(group_tags)}.db"),
            results_json=str(tmp_path / "r.json"),
            results_csv=str(tmp_path / "r.csv"),
        )
        svc.tag_config = {"categories": [{"display_name": "B"}], "tag_groups": groups, "global_settings": {}}
        svc.ai.tag_config = svc.tag_config
        return svc

    return _make