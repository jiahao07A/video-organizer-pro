# -*- coding: utf-8 -*-
"""标签组写入 / 回填修复。"""
from pathlib import Path

from core.video_organizer_service import DEFAULT_SETTINGS, VideoOrganizerService
from core.tag_vocab import build_vocab_draft_from_lines, merge_draft_into_tag_config


def test_heal_tag_config_from_db(tmp_path: Path):
    svc = VideoOrganizerService(
        settings={**DEFAULT_SETTINGS},
        on_log=lambda _m: None,
        db_path=str(tmp_path / "t.db"),
        results_json=str(tmp_path / "r.json"),
        results_csv=str(tmp_path / "r.csv"),
    )
    svc.tag_config = {
        "tag_groups": [
            {"id": "mood", "name": "氛围", "tags": [], "rules": {}},
            {"id": "subject", "name": "主体", "tags": [], "rules": {}},
        ]
    }
    svc.db.add_tag("mood", "治愈", is_learned=0)
    svc.db.add_tag("mood", "紧张", is_learned=0)
    svc.db.add_tag("subject", "男性", is_learned=0)

    assert svc.heal_tag_config_tags_from_db() is True
    mood = next(g for g in svc.tag_config["tag_groups"] if g["id"] == "mood")
    assert "治愈" in mood["tags"]
    assert "紧张" in mood["tags"]
    sub = next(g for g in svc.tag_config["tag_groups"] if g["id"] == "subject")
    assert "男性" in sub["tags"]


def test_merge_creates_missing_groups():
    cfg = {"tag_groups": []}
    draft = build_vocab_draft_from_lines(
        [
            "# ========== 氛围 mood ==========",
            "开心 ← 快乐",
        ]
    )
    merged = merge_draft_into_tag_config(cfg, draft, replace_all_group_tags=True)
    ids = [g["id"] for g in merged["tag_groups"]]
    assert "mood" in ids
    mood = next(g for g in merged["tag_groups"] if g["id"] == "mood")
    assert "开心" in mood["tags"]