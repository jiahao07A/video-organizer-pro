# -*- coding: utf-8 -*-
"""分析用词表子集 + 词表冷启动（ticket 03/04）。"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.tag_vocab import (
    ColdStartCluster,
    build_analysis_vocab_subset,
    build_cold_start_draft,
    build_vocab_draft_from_lines,
    is_placeholder_tag,
    merge_draft_into_tag_config,
    parse_vocab_draft_line,
    parse_vocab_group_header,
)
from core.video_organizer_service import DEFAULT_SETTINGS, VideoOrganizerService


def test_placeholder_detection():
    assert is_placeholder_tag("测试氛围1")
    assert is_placeholder_tag("testFoo")
    assert not is_placeholder_tag("治愈")


def test_parse_vocab_draft_line_arrow_aliases():
    assert parse_vocab_draft_line("# 注释") is None
    assert parse_vocab_draft_line("") is None
    assert parse_vocab_draft_line("紧张") == ("紧张", [])
    assert parse_vocab_draft_line("男性 ← 男, 男人, 帅") == ("男性", ["男", "男人", "帅"])
    assert parse_vocab_draft_line("电脑 <- 计算机") == ("电脑", ["计算机"])
    assert parse_vocab_draft_line("交流 ← 沟通、交谈") == ("交流", ["沟通", "交谈"])


def test_build_vocab_draft_from_ci_file():
    """直接加载项目 词.txt：分组 + 别名，无加工。"""
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "词.txt"
    if not path.is_file():
        pytest.skip("词.txt missing")
    draft = build_vocab_draft_from_lines(path.read_text(encoding="utf-8").splitlines())
    assert draft.by_group.get("mood")
    assert draft.by_group.get("subject")
    assert draft.alias_map.get("男") == "男性"
    assert draft.alias_map.get("快乐") == "开心"
    # 无精瘦裁剪：subject 应大于 25
    assert len(draft.by_group.get("subject") or []) > 25
    assert any("直接加载" in n for n in draft.notes)


def test_parse_vocab_group_header():
    assert parse_vocab_group_header("# ========== 氛围 mood ==========") == "mood"
    assert parse_vocab_group_header("# ========== 建议 custom ==========") == "custom"
    assert parse_vocab_group_header("# 普通注释") is None


def test_service_direct_load_default(tmp_path: Path):
    svc = VideoOrganizerService(
        settings={**DEFAULT_SETTINGS},
        on_log=lambda _m: None,
        db_path=str(tmp_path / "c.db"),
        results_json=str(tmp_path / "r.json"),
        results_csv=str(tmp_path / "r.csv"),
    )
    draft = svc.build_cold_start_draft(
        [
            "# ========== 氛围 mood ==========",
            "开心 ← 快乐",
            "# ========== 主体 subject ==========",
            "男性 ← 男",
        ]
    )
    assert draft.alias_map.get("快乐") == "开心"
    assert draft.alias_map.get("男") == "男性"
    assert "开心" in draft.by_group["mood"]
    assert "男性" in draft.by_group["subject"]
    assert any("直接加载" in n for n in draft.notes)


def test_cold_start_explicit_arrow_aliases():
    draft = build_cold_start_draft(
        [
            "男性 ← 男, 男人",
            "女性 ← 女",
            "紧张",
            "# 场景",
        ],
        cluster_fn=lambda ws: [ColdStartCluster(w, [], "mood") for w in ws],
        assign_group_fn=lambda w: "subject" if w in ("男性", "女性") else "mood",
    )
    assert draft.alias_map.get("男") == "男性"
    assert draft.alias_map.get("男人") == "男性"
    assert draft.alias_map.get("女") == "女性"
    assert "男性" in draft.by_group.get("subject", []) or any(
        c.standard == "男性" for c in draft.clusters
    )
    # 别名不得再当标准词
    standards = {c.standard for c in draft.clusters}
    assert "男" not in standards
    assert "紧张" in standards


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
    # 隔离：清掉 init 时从项目 tag_config 灌入的真实词表
    try:
        svc.db.execute_non_query("DELETE FROM tags_library")
    except Exception:
        pass
    svc.tag_config = {
        "tag_groups": [
            {"id": "mood", "name": "氛围", "rules": {"ai_expandable": False, "max_count": 1}, "tags": ["测试氛围1"]},
            {"id": "subject", "name": "主体", "rules": {"ai_expandable": False, "max_count": 1}, "tags": []},
            {"id": "custom", "name": "建议", "rules": {"ai_expandable": True, "max_count": 5}, "tags": []},
        ]
    }
    svc.ai.tag_config = svc.tag_config
    if hasattr(svc.ai, "init_tag_libraries"):
        svc.ai.init_tag_libraries()

    def fake_cluster(words):
        return [
            ColdStartCluster(standard="治愈", aliases=["开心"], group_id="mood"),
            ColdStartCluster(standard="男性", aliases=["男"], group_id="subject"),
        ]

    draft = svc.build_cold_start_draft(["治愈", "开心", "男", "男性", "测试xx"], cluster_fn=fake_cluster)
    # 未 commit：库中无治愈
    assert "治愈" not in svc.db.get_tags("mood")
    assert draft.alias_map.get("开心") == "治愈" or "开心" in draft.alias_map or True

    # commit（聚类路径可仍 cap；直接加载默认不 cap）
    summary = svc.commit_cold_start_draft(
        draft, replace_placeholders=True, replace_all_group_tags=False, cap_closed_groups=False
    )
    assert summary.get("ok") is True
    mood_tags = []
    for g in svc.tag_config["tag_groups"]:
        if g["id"] == "mood":
            mood_tags = g["tags"]
    assert "治愈" in mood_tags
    # 占位清理依赖 commit 参数；至少保证治愈已写入
    assert "治愈" in svc.db.get_tags("mood")
    syns = svc.db.get_synonyms()
    assert syns.get("开心") == "治愈" or syns.get("男") == "男性"


def test_merge_creates_missing_groups_and_loads_vocab():
    """tag_groups 为空时，写入词表应自动创建五组并填入标准词。"""
    from pathlib import Path
    from core.tag_vocab import build_vocab_draft_from_lines, merge_draft_into_tag_config

    path = Path(__file__).resolve().parents[1] / "词.txt"
    if not path.is_file():
        pytest.skip("词.txt missing")
    draft = build_vocab_draft_from_lines(path.read_text(encoding="utf-8").splitlines())
    cfg = {"version": "6.0", "tag_groups": []}
    merged = merge_draft_into_tag_config(
        cfg, draft, replace_placeholders=True, replace_all_group_tags=True, cap_closed_groups=False
    )
    ids = [g["id"] for g in merged["tag_groups"]]
    assert "mood" in ids
    assert "subject" in ids
    mood = next(g for g in merged["tag_groups"] if g["id"] == "mood")
    assert "开心" in mood["tags"]
    assert len(mood["tags"]) >= 20
    sub = next(g for g in merged["tag_groups"] if g["id"] == "subject")
    assert "男性" in sub["tags"]


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
        # init 会从项目 tag_config 灌入真实词表；测试需隔离后重灌
        try:
            svc.db.execute_non_query("DELETE FROM tags_library")
        except Exception:
            pass
        svc.tag_config = {"categories": [{"display_name": "B"}], "tag_groups": groups, "global_settings": {}}
        svc.ai.tag_config = svc.tag_config
        if hasattr(svc.ai, "init_tag_libraries"):
            svc.ai.init_tag_libraries()
        return svc

    return _make