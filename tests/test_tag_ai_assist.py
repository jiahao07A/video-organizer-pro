# -*- coding: utf-8 -*-
"""标签库 AI 辅助规则边界（ticket 06/08）。"""
from core.tag_ai_assist import (
    apply_synonym_merges_plan,
    rule_pending_suggestion,
    rule_synonym_audit,
)


def test_pending_link_alias_when_substring():
    row = {"id": 1, "raw_text": "男", "group_id": "subject"}
    s = rule_pending_suggestion(row, ["男性", "女性"])
    assert s.action == "link_alias"
    assert s.recommended_standard == "男性"


def test_pending_approve_when_no_match():
    row = {"id": 2, "raw_text": "赛博朋克", "group_id": "mood"}
    s = rule_pending_suggestion(row, ["治愈", "紧张"])
    assert s.action == "approve_standard"
    assert s.recommended_group_id == "mood"


def test_synonym_audit_containment():
    sug = rule_synonym_audit(["男", "男性", "办公"])
    keeps = {s.keep: s.merge_as_aliases for s in sug}
    assert "男" in keeps
    assert "男性" in keeps["男"]


def test_apply_merges_plan():
    from core.tag_ai_assist import SynonymMergeSuggestion

    plan = apply_synonym_merges_plan(
        [SynonymMergeSuggestion(keep="男性", merge_as_aliases=["男", "男人"])]
    )
    assert plan["男"] == "男性"
    assert plan["男人"] == "男性"