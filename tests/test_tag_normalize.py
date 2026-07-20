# -*- coding: utf-8 -*-
"""标签归一主接缝测试（ticket 02 / ADR-0004）。"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.tag_normalize import (
    GroupSpec,
    expand_tags_for_filter,
    group_specs_from_tag_config,
    normalize_flat_tags,
    normalize_grouped_tags,
    resolve_to_standard,
)
from core.video_organizer_service import DEFAULT_SETTINGS, VideoOrganizerService
from gui.models.proxy_model import video_matches_filter


def test_alias_maps_to_standard():
    assert resolve_to_standard("男", {"男": "男性"}, standards={"男性", "女性"}) == "男性"
    assert resolve_to_standard("男性", {"男": "男性"}, standards={"男性"}) == "男性"
    assert resolve_to_standard("未知", {"男": "男性"}, standards={"男性"}) is None


def test_closed_group_drops_unknown_and_pending():
    groups = [
        GroupSpec("mood", max_count=1, ai_expandable=False, standards=frozenset({"治愈", "紧张"})),
        GroupSpec("custom", max_count=5, ai_expandable=True, standards=frozenset()),
    ]
    raw = {"mood": ["赛博朋克", "治愈"], "custom": ["手持特写"]}
    result = normalize_grouped_tags(raw, groups, {})
    assert result.tag_groups["mood"] == ["治愈"]
    assert result.tag_groups["custom"] == ["手持特写"]
    assert "赛博朋克" not in result.tags
    assert any(p.raw_text == "赛博朋克" and p.group_id == "mood" for p in result.pending)
    # 建议组库外词不进 pending（仅挂视频）
    assert not any(p.raw_text == "手持特写" for p in result.pending)


def test_no_fuzzy_nearest_match():
    """禁止把库外词贴到「最像」的标准词（旧 _simple_fuzzy_match 会把「治」贴成「治愈」）。"""
    groups = [GroupSpec("mood", 1, False, frozenset({"治愈", "紧张"}))]
    result = normalize_grouped_tags({"mood": ["治"]}, groups, {})
    assert result.tag_groups["mood"] == []
    assert any(p.raw_text == "治" for p in result.pending)


def test_alias_in_closed_group():
    groups = [GroupSpec("subject", 1, False, frozenset({"男性", "女性"}))]
    result = normalize_grouped_tags({"subject": ["男人"]}, groups, {"男人": "男性", "男": "男性"})
    assert result.tag_groups["subject"] == ["男性"]
    assert result.pending == []


def test_max_count_truncation():
    groups = [
        GroupSpec("mood", 1, False, frozenset({"a", "b", "c"})),
        GroupSpec("custom", 2, True, frozenset()),
    ]
    result = normalize_grouped_tags(
        {"mood": ["a", "b"], "custom": ["x", "y", "z"]},
        groups,
        {},
    )
    assert result.tag_groups["mood"] == ["a"]
    assert result.tag_groups["custom"] == ["x", "y"]


def test_suggestion_does_not_require_library():
    groups = [GroupSpec("custom", 5, True, frozenset({"已有"}))]
    result = normalize_grouped_tags({"custom": ["全新细节词"]}, groups, {})
    assert result.tag_groups["custom"] == ["全新细节词"]
    assert result.pending == []


def test_expand_filter_alias_hits_standard():
    amap = {"男": "男性", "男人": "男性"}
    expanded = expand_tags_for_filter(["男"], amap)
    assert "男" in expanded and "男性" in expanded
    v = {"tags": ["男性", "室内"]}
    assert video_matches_filter(v, {"tags": ["男"], "alias_map": amap, "tag_match_mode": "any"})
    assert not video_matches_filter(v, {"tags": ["女"], "alias_map": amap})


def test_group_specs_from_config():
    cfg = {
        "tag_groups": [
            {
                "id": "mood",
                "rules": {"max_count": 1, "ai_expandable": False},
                "tags": [{"name": "治愈"}, "紧张"],
            }
        ]
    }
    specs = group_specs_from_tag_config(cfg)
    assert len(specs) == 1
    assert specs[0].standards == frozenset({"治愈", "紧张"})


def test_normalize_flat_alias():
    assert normalize_flat_tags(["男", "室内"], {"男": "男性"}) == ["男性", "室内"]


@pytest.fixture
def service(tmp_path: Path) -> VideoOrganizerService:
    cfg = {
        "version": "6.0",
        "global_settings": {"system_prompt": "test"},
        "categories": [{"id": "broll", "display_name": "B-Roll"}],
        "tag_groups": [
            {
                "id": "mood",
                "name": "氛围",
                "rules": {"selection_mode": "single", "max_count": 1, "ai_expandable": False, "local_prompt": "基调"},
                "tags": ["治愈", "紧张"],
            },
            {
                "id": "subject",
                "name": "主体",
                "rules": {"selection_mode": "single", "max_count": 1, "ai_expandable": False, "local_prompt": "主体"},
                "tags": ["男性", "女性"],
            },
            {
                "id": "custom",
                "name": "建议",
                "rules": {"selection_mode": "multiple", "max_count": 5, "ai_expandable": True, "local_prompt": "细节"},
                "tags": [],
            },
        ],
    }
    svc = VideoOrganizerService(
        settings={**DEFAULT_SETTINGS, "ui_preferences": dict(DEFAULT_SETTINGS.get("ui_preferences", {}))},
        on_log=lambda _m: None,
        db_path=str(tmp_path / "norm.db"),
        results_json=str(tmp_path / "r.json"),
        results_csv=str(tmp_path / "r.csv"),
    )
    svc.tag_config = cfg
    svc.ai.tag_config = cfg
    svc.db.add_synonym("男性", "男")
    svc.db.add_synonym("男性", "男人")
    return svc


def test_analyze_path_normalize_no_fuzzy_no_auto_library(service: VideoOrganizerService):
    """模拟 AI 原始输出经 analyze 清洗：封闭组硬过滤、建议组可保留、不自动进库。"""
    raw = {
        "category": "B-Roll",
        "summary": "测试",
        "mood": "治",  # 模糊近似，必须丢弃
        "subject": "男人",  # 别名 → 男性
        "custom": "手持跟拍",
    }
    cleaned = service.ai.apply_tag_normalization(raw)
    assert cleaned["tag_groups"]["mood"] == []
    assert cleaned["tag_groups"]["subject"] == ["男性"]
    assert cleaned["tag_groups"]["custom"] == ["手持跟拍"]
    assert "治" not in cleaned["tags"]
    assert "男性" in cleaned["tags"]
    # 建议组未写入 tags_library
    lib = service.db.get_tags("custom")
    assert "手持跟拍" not in lib
    # 待审有「治」
    pending = service.db.list_pending_tags(status="pending")
    assert any(p["raw_text"] == "治" and p["group_id"] == "mood" for p in pending)


def test_bulk_replace_runs_alias_normalize(service: VideoOrganizerService, tmp_path: Path):
    path = str((tmp_path / "v.mp4").resolve())
    Path(path).write_bytes(b"")
    service.db.upsert_video(
        {
            "path": path,
            "filename": "v.mp4",
            "tags": ["男人", "室内"],
            "status": "analyzed",
        }
    )
    service.bulk_replace_tags("男人", "男")  # 替换成别名，落库应归一为标准词
    rows = service.db.get_all_videos()
    tags = rows[0]["tags"]
    assert "男性" in tags
    assert "男" not in tags
    assert "男人" not in tags