# -*- coding: utf-8 -*-
"""标签库工作视图纯逻辑 + 服务边界（不测像素）。

覆盖：
- 待审审核视图的搜索 / 建议组筛选；
- 批量 AI 建议行只在明确勾选后才进入「应用所选」集合（服务层边界）；
- 标准词管理视图行合成（标准词 / 别名 / 使用次数 / 所属组）与筛选；
- 服务层 list/batch_suggest/apply_selected/resolve 的人工确认边界。
"""
from __future__ import annotations

import os
import tempfile

import pytest

from core.tag_ai_assist import PendingTagSuggestion
from core.model_providers import ensure_providers
from core.video_organizer_service import SettingsManager, VideoOrganizerService
from gui.models.tag_work_views import (
    PendingSuggestionRow,
    StandardWordRow,
    build_pending_suggestion_rows,
    build_standard_word_rows,
    filter_pending_rows,
    filter_standard_word_rows,
    pending_suggestion_action_label,
    selected_suggestions,
)


# --- 待审审核视图纯逻辑 ---------------------------------------------------


def _pending(*rows):
    return [
        {"id": i + 1, "raw_text": raw, "group_id": gid, "status": "pending"}
        for i, (raw, gid) in enumerate(rows)
    ]


def test_filter_pending_by_query_and_group():
    rows = _pending(("赛博朋克", "mood"), ("男孩", "subject"), ("野词", ""))
    assert len(filter_pending_rows(rows)) == 3
    # 关键字命中原文
    hit = filter_pending_rows(rows, query="赛博")
    assert [r["raw_text"] for r in hit] == ["赛博朋克"]
    # 大小写不敏感且命中建议组 id
    assert len(filter_pending_rows(rows, query="SUBJECT")) == 1
    # 按建议组过滤
    assert [
        r["raw_text"] for r in filter_pending_rows(rows, group_id="mood")
    ] == ["赛博朋克"]
    # 只看无建议组
    assert [
        r["raw_text"]
        for r in filter_pending_rows(rows, group_id="__none__")
    ] == ["野词"]
    # 组合
    assert filter_pending_rows(rows, query="男", group_id="mood") == []


def test_pending_suggestion_rows_default_unchecked_and_action_label():
    sug = PendingTagSuggestion(
        pending_id=1,
        raw_text="男",
        group_id="subject",
        action="link_alias",
        recommended_standard="男性",
        reason="近义",
        source="model",
    )
    rows = build_pending_suggestion_rows([sug])
    assert len(rows) == 1
    r = rows[0]
    assert r.checked is False
    assert r.source_label == "模型"
    assert "挂别名" in r.action_label and "男性" in r.action_label
    # 未勾选 → 空集合（服务层边界）
    assert selected_suggestions(rows) == []

    approve = PendingTagSuggestion(
        pending_id=2,
        raw_text="赛博",
        group_id="mood",
        action="approve_standard",
        recommended_group_id="mood",
    )
    assert pending_suggestion_action_label(approve) == "批准→mood"
    discard = PendingTagSuggestion(
        pending_id=3, raw_text="x", group_id="", action="discard"
    )
    assert pending_suggestion_action_label(discard) == "丢弃"


def test_selected_suggestions_only_checked():
    a = PendingSuggestionRow(suggestion="A", checked=True)
    b = PendingSuggestionRow(suggestion="B", checked=False)
    c = PendingSuggestionRow(suggestion="C", checked=True)
    assert selected_suggestions([a, b, c]) == ["A", "C"]


# --- 标准词管理视图纯逻辑 -------------------------------------------------


def test_build_standard_word_rows_with_aliases_and_usage():
    cfg = {
        "tag_groups": [
            {"id": "mood", "name": "氛围", "tags": ["治愈", "紧张"]},
            {"id": "subject", "name": "主体", "tags": [{"name": "男性"}]},
            {"id": "pool", "name": "中转池", "tags": ["不应出现"]},
        ]
    }
    details = [
        {"id": 11, "tag_name": "治愈", "dimension": "mood", "usage_count": 5},
        {"id": 12, "tag_name": "男性", "dimension": "subject", "usage_count": 2},
    ]
    syns = {"男": "男性", "男人": "男性", "治愈系": "治愈"}
    rows = build_standard_word_rows(cfg, details, syns)
    by_name = {r.name: r for r in rows}
    assert "不应出现" not in by_name  # pool 不展示为标准词
    assert by_name["治愈"].usage_count == 5
    assert by_name["治愈"].aliases == ["治愈系"]
    assert by_name["男性"].group_name == "主体"
    assert by_name["男性"].aliases == ["男", "男人"]
    assert by_name["男性"].alias_text == "男、男人"
    assert by_name["男性"].tag_id == 12


def test_filter_standard_rows_by_group_and_alias():
    rows = [
        StandardWordRow(name="治愈", group_id="mood", group_name="氛围", aliases=["治愈系"]),
        StandardWordRow(name="男性", group_id="subject", group_name="主体", aliases=["男"]),
    ]
    assert len(filter_standard_word_rows(rows)) == 2
    assert [r.name for r in filter_standard_word_rows(rows, group_id="mood")] == ["治愈"]
    # 别名命中
    assert [r.name for r in filter_standard_word_rows(rows, query="男")] == ["男性"]
    assert filter_standard_word_rows(rows, query="治愈系")[0].name == "治愈"
    assert filter_standard_word_rows(rows, query="zzz") == []


# --- 服务层边界（人工确认 / 无中转池 / 目标组必选） ----------------------


@pytest.fixture()
def svc(tmp_path):
    s = SettingsManager.deep_copy_defaults()
    ensure_providers(s)
    service = VideoOrganizerService(
        settings=s,
        on_log=lambda _m: None,
        db_path=str(tmp_path / "t.db"),
        results_json=str(tmp_path / "r.json"),
        results_csv=str(tmp_path / "r.csv"),
    )
    service.tag_config = {
        "tag_groups": [
            {"id": "mood", "name": "氛围", "tags": [], "rules": {}},
            {"id": "subject", "name": "主体", "tags": [], "rules": {}},
        ],
        "global_settings": {},
    }
    service.save_tag_config(service.tag_config)
    return service


def test_list_pending_and_batch_suggest_do_not_write(svc):
    svc.db.add_pending_tag("赛博朋克", group_id="mood")
    svc.db.add_pending_tag("男孩", group_id="subject")
    rows = svc.db.list_pending_tags(status="pending")
    assert {r["raw_text"] for r in rows} == {"赛博朋克", "男孩"}
    # 批量建议（规则降级，不打模型）不写库
    suggestions = svc.batch_suggest_pending_tags(use_model=False)
    assert len(suggestions) == 2
    still = svc.db.list_pending_tags(status="pending")
    assert len(still) == 2


def test_apply_selected_only_writes_checked(svc):
    svc.db.add_pending_tag("男孩", group_id="subject")
    svc.db.add_pending_tag("赛博朋克", group_id="mood")
    rows = svc.db.list_pending_tags(status="pending")
    by_raw = {r["raw_text"]: r for r in rows}

    discard = PendingTagSuggestion(
        pending_id=by_raw["男孩"]["id"],
        raw_text="男孩",
        group_id="subject",
        action="discard",
    )
    # 只应用「男孩」这一条；赛博朋克未选中不应变化
    result = svc.apply_pending_suggestions_selected([discard], confirm=True)
    assert result["applied"] == 1
    remaining = svc.db.list_pending_tags(status="pending")
    assert {r["raw_text"] for r in remaining} == {"赛博朋克"}


def test_apply_selected_requires_confirm(svc):
    svc.db.add_pending_tag("男孩", group_id="subject")
    pid = svc.db.list_pending_tags(status="pending")[0]["id"]
    sug = PendingTagSuggestion(
        pending_id=pid, raw_text="男孩", group_id="subject", action="discard"
    )
    assert svc.apply_pending_suggestions_selected([sug], confirm=False)["applied"] == 0
    assert len(svc.db.list_pending_tags(status="pending")) == 1


def test_approve_requires_valid_target_group(svc):
    svc.db.add_pending_tag("赛博朋克", group_id="mood")
    pid = svc.db.list_pending_tags(status="pending")[0]["id"]
    # pool / 未知组均拒绝（缺省时回退到该待审词的建议组，是既有语义）
    assert svc.resolve_pending_tag(pid, "approve_standard", group_id="pool") is False
    assert svc.resolve_pending_tag(pid, "approve_standard", group_id="nope") is False
    assert len(svc.db.list_pending_tags(status="pending")) == 1
    # 合法组：批准为标准词并落库
    assert svc.resolve_pending_tag(pid, "approve_standard", group_id="mood") is True
    names = [
        r["tag_name"]
        for r in svc.db.get_tags_detail()
        if str(r.get("dimension") or "").lower() == "mood"
    ]
    assert "赛博朋克" in names
    assert svc.db.list_pending_tags(status="pending") == []
