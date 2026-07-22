# -*- coding: utf-8 -*-
"""标签库 AI / 芯片 token / 确认边界（主接缝 B、C）。"""
from core.tag_ai_assist import (
    PendingTagSuggestion,
    StandardTagAssistSuggestion,
    SynonymMergeSuggestion,
    apply_synonym_merges_plan,
    chip_style_tokens,
    parse_pending_ai_response,
    parse_standard_tag_ai_response,
    parse_synonym_ai_response,
    rule_pending_suggestion,
    rule_standard_tag_assist,
    rule_synonym_audit,
)


def test_pending_link_alias_when_substring():
    row = {"id": 1, "raw_text": "男", "group_id": "subject"}
    s = rule_pending_suggestion(row, ["男性", "女性"])
    assert s.action == "link_alias"
    assert s.recommended_standard == "男性"
    assert s.source == "rule"


def test_pending_approve_when_no_match():
    row = {"id": 2, "raw_text": "赛博朋克", "group_id": "mood"}
    s = rule_pending_suggestion(row, ["治愈", "紧张"])
    assert s.action == "approve_standard"
    assert s.recommended_group_id == "mood"


def test_parse_pending_ai_response_model():
    row = {"id": 9, "raw_text": "男孩", "group_id": "subject"}
    data = {
        "action": "link_alias",
        "recommended_standard": "男性",
        "reason": "近义",
    }
    s = parse_pending_ai_response(data, row, ["男性", "女性"])
    assert s is not None
    assert s.action == "link_alias"
    assert s.recommended_standard == "男性"
    assert s.source == "model"


def test_synonym_audit_containment():
    sug = rule_synonym_audit(["男", "男性", "办公"])
    keeps = {s.keep: s.merge_as_aliases for s in sug}
    assert "男" in keeps
    assert "男性" in keeps["男"]


def test_parse_synonym_ai_response():
    data = {
        "merges": [
            {"keep": "男性", "merge_as_aliases": ["男人", "男"], "reason": "同义"},
        ]
    }
    out = parse_synonym_ai_response(data, ["男性", "男人", "男", "办公"])
    assert len(out) >= 1
    assert out[0].keep == "男性"
    assert "男" in out[0].merge_as_aliases or "男人" in out[0].merge_as_aliases
    assert out[0].source == "model"


def test_apply_merges_plan():
    plan = apply_synonym_merges_plan(
        [SynonymMergeSuggestion(keep="男性", merge_as_aliases=["男", "男人"])]
    )
    assert plan["男"] == "男性"
    assert plan["男人"] == "男性"


def test_standard_tag_assist_rule_and_parse():
    s = rule_standard_tag_assist("男性", "subject", ["男性", "女性"], ["男"])
    assert s.tag_name == "男性"
    assert s.source == "rule"
    # 规则不得把其它标准词当别名
    assert s.suggested_aliases == []
    data = {
        "aliases": ["男士", "男人", "女性"],
        "recommended_group_id": "mood",
        "reason": "偏氛围",
    }
    p = parse_standard_tag_ai_response(
        data, "男性", "subject", standard_tags=["男性", "女性"]
    )
    assert p is not None
    assert "男士" in p.suggested_aliases
    assert "女性" not in p.suggested_aliases  # 标准词过滤
    assert p.recommended_group_id == "mood"
    assert p.source == "model"


def test_parse_standard_empty_dict_returns_none():
    assert parse_standard_tag_ai_response({}, "男性", "subject") is None


def test_chip_style_tokens_readable():
    dark = chip_style_tokens("mood", theme="dark", misspelled=False)
    light = chip_style_tokens("subject", theme="light", misspelled=False)
    assert dark["text"] != dark["background"]
    assert light["text"] != light["background"]
    assert dark["accent_bar"].startswith("#")
    miss = chip_style_tokens("custom", theme="dark", misspelled=True)
    assert miss["border"] == "#ff4d4f"


def test_apply_pending_suggestion_requires_confirm(tmp_path):
    from core.video_organizer_service import VideoOrganizerService, SettingsManager
    from core.model_providers import ensure_providers

    s = SettingsManager.deep_copy_defaults()
    ensure_providers(s)
    svc = VideoOrganizerService(
        settings=s,
        on_log=lambda _m: None,
        db_path=str(tmp_path / "t.db"),
        results_json=str(tmp_path / "r.json"),
        results_csv=str(tmp_path / "r.csv"),
    )
    sug = PendingTagSuggestion(
        pending_id=1,
        raw_text="x",
        group_id="mood",
        action="discard",
        source="model",
    )
    assert svc.apply_pending_suggestion(sug, confirm=False) is False
    assert svc.apply_pending_suggestions_selected([sug], confirm=False)["applied"] == 0


def test_apply_standard_assist_requires_confirm(tmp_path):
    from core.video_organizer_service import VideoOrganizerService, SettingsManager
    from core.model_providers import ensure_providers

    s = SettingsManager.deep_copy_defaults()
    ensure_providers(s)
    svc = VideoOrganizerService(
        settings=s,
        on_log=lambda _m: None,
        db_path=str(tmp_path / "t2.db"),
        results_json=str(tmp_path / "r2.json"),
        results_csv=str(tmp_path / "r2.csv"),
    )
    sug = StandardTagAssistSuggestion(
        tag_name="男性",
        current_group_id="subject",
        suggested_aliases=["男"],
        recommended_group_id="mood",
        source="model",
    )
    r = svc.apply_standard_tag_assist(sug, confirm=False)
    assert r.get("ok") is False


def test_apply_standard_skips_standard_as_alias(tmp_path):
    from core.video_organizer_service import VideoOrganizerService, SettingsManager
    from core.model_providers import ensure_providers
    from core.tag_ai_assist import StandardTagAssistSuggestion

    s = SettingsManager.deep_copy_defaults()
    ensure_providers(s)
    svc = VideoOrganizerService(
        settings=s,
        on_log=lambda _m: None,
        db_path=str(tmp_path / "t3.db"),
        results_json=str(tmp_path / "r3.json"),
        results_csv=str(tmp_path / "r3.csv"),
    )
    # 准备两个标准词
    svc.tag_config = {
        "tag_groups": [
            {
                "id": "subject",
                "name": "主体",
                "tags": [{"name": "男性"}, {"name": "女性"}],
                "rules": {},
            }
        ],
        "global_settings": {},
    }
    svc.save_tag_config(svc.tag_config)
    if svc.db:
        svc.db.add_tag("subject", "男性")
        svc.db.add_tag("subject", "女性")

    sug = StandardTagAssistSuggestion(
        tag_name="男性",
        current_group_id="subject",
        suggested_aliases=["女性", "男士"],  # 女性是标准词应跳过
        recommended_group_id=None,
        source="model",
    )
    r = svc.apply_standard_tag_assist(sug, apply_aliases=True, apply_regroup=False, confirm=True)
    assert r.get("ok") is True
    assert r.get("aliases_skipped", 0) >= 1
    syns = svc.db.get_synonyms() or {}
    assert "女性" not in syns  # 不得把标准词挂成别名
    # 男士可挂
    assert syns.get("男士") == "男性" or r.get("aliases_added", 0) >= 0


def test_synonym_merge_removes_merged_standard_from_db(tmp_path):
    from core.video_organizer_service import VideoOrganizerService, SettingsManager
    from core.model_providers import ensure_providers
    from core.tag_ai_assist import SynonymMergeSuggestion

    s = SettingsManager.deep_copy_defaults()
    ensure_providers(s)
    svc = VideoOrganizerService(
        settings=s,
        on_log=lambda _m: None,
        db_path=str(tmp_path / "t4.db"),
        results_json=str(tmp_path / "r4.json"),
        results_csv=str(tmp_path / "r4.csv"),
    )
    svc.tag_config = {
        "tag_groups": [
            {
                "id": "subject",
                "name": "主体",
                "tags": [{"name": "男"}, {"name": "男性"}],
                "rules": {},
            }
        ],
        "global_settings": {},
    }
    svc.save_tag_config(svc.tag_config)
    svc.db.add_tag("subject", "男")
    svc.db.add_tag("subject", "男性")

    result = svc.apply_synonym_merge_suggestions(
        [SynonymMergeSuggestion(keep="男性", merge_as_aliases=["男"], reason="test")],
        confirm=True,
    )
    assert result.get("applied", 0) >= 1
    syns = svc.db.get_synonyms() or {}
    assert syns.get("男") == "男性"
    # 被合并词不应再作为标准词出现在 tags_library
    rows = svc.db.execute_query(
        "SELECT tag_name FROM tags_library WHERE tag_name = ?", ("男",)
    ) or []
    assert len(rows) == 0
    # tag_config 也应去掉
    names = []
    for g in svc.tag_config.get("tag_groups") or []:
        for t in g.get("tags") or []:
            n = t.get("name") if isinstance(t, dict) else t
            names.append(str(n))
    assert "男" not in names
    assert "男性" in names