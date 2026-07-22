# -*- coding: utf-8 -*-
"""S4：废除中转池 + 组归属规则（ticket 05）。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.tag_group_ops import (
    POOL_GROUP_ID,
    can_delete_tag_group,
    known_group_ids,
    migrate_pool_tags_to_pending,
    reassign_and_remove_group,
    reject_pool_in_classified,
    strip_names_from_tag_groups,
    validate_standard_tag_group_id,
)
from core.tag_import_service import TagImportService
from core.video_organizer_service import (
    SettingsManager,
    VideoOrganizerService,
)


SAMPLE_GROUPS = [
    {"id": "mood", "name": "氛围", "tags": ["治愈", "紧张"]},
    {"id": "subject", "name": "主体", "tags": ["男性"]},
    {"id": "custom", "name": "建议", "tags": []},
]


def test_validate_rejects_missing_and_pool():
    known = known_group_ids(SAMPLE_GROUPS)
    ok, _ = validate_standard_tag_group_id(None, known)
    assert not ok
    ok, _ = validate_standard_tag_group_id("", known)
    assert not ok
    ok, _ = validate_standard_tag_group_id("pool", known)
    assert not ok
    ok, _ = validate_standard_tag_group_id(POOL_GROUP_ID, known)
    assert not ok
    ok, _ = validate_standard_tag_group_id("unknown_x", known)
    assert not ok
    ok, _ = validate_standard_tag_group_id("mood", known)
    assert ok


def test_migrate_pool_and_empty_to_pending():
    tags = [
        {"id": 1, "tag_name": "池词A", "dimension": "pool"},
        {"id": 2, "tag_name": "空维B", "dimension": ""},
        {"id": 3, "tag_name": "治愈", "dimension": "mood"},
        {"id": 4, "tag_name": "野词C", "dimension": "orphan_dim"},
    ]
    pending = [{"raw_text": "已有待审", "status": "pending"}]
    known = known_group_ids(SAMPLE_GROUPS)
    r = migrate_pool_tags_to_pending(tags, pending, known)
    assert "池词A" in r.to_pending
    assert "空维B" in r.to_pending
    assert "野词C" in r.to_pending
    assert "治愈" not in r.to_pending
    assert set(r.remove_library_names) == {"池词A", "空维B", "野词C"}
    assert 1 in r.remove_library_ids


def test_migrate_skips_duplicate_pending():
    tags = [{"id": 9, "tag_name": "重复词", "dimension": "pool"}]
    pending = [{"raw_text": "重复词", "status": "pending"}]
    r = migrate_pool_tags_to_pending(tags, pending, known_group_ids(SAMPLE_GROUPS))
    assert "重复词" not in r.to_pending
    assert "重复词" in r.remove_library_names


def test_strip_names_from_groups():
    groups = [
        {"id": "mood", "tags": ["治愈", "池词A"]},
        {"id": "subject", "tags": ["男性"]},
    ]
    out = strip_names_from_tag_groups(groups, ["池词A"])
    assert out[0]["tags"] == ["治愈"]
    assert out[1]["tags"] == ["男性"]


def test_cannot_delete_last_group():
    groups = [{"id": "mood", "tags": []}]
    v = can_delete_tag_group(groups, "mood")
    assert not v.allowed
    assert "唯一" in v.reason


def test_empty_group_deletable():
    groups = [
        {"id": "mood", "tags": ["a"]},
        {"id": "subject", "tags": []},
    ]
    v = can_delete_tag_group(groups, "subject")
    assert v.allowed
    assert not v.needs_reassign
    ok, new_g, err, moved = reassign_and_remove_group(groups, "subject")
    assert ok and not err
    assert moved == []
    assert [g["id"] for g in new_g] == ["mood"]


def test_nonempty_requires_reassign():
    groups = [
        {"id": "mood", "tags": ["治愈", "紧张"]},
        {"id": "subject", "tags": ["男性"]},
    ]
    v = can_delete_tag_group(groups, "mood")
    assert not v.allowed
    assert v.needs_reassign

    v2 = can_delete_tag_group(groups, "mood", target_group_id="pool")
    assert not v2.allowed

    ok, new_g, err, moved = reassign_and_remove_group(
        groups, "mood", target_group_id="subject"
    )
    assert ok and not err
    assert set(moved) == {"治愈", "紧张"}
    by = {g["id"]: g["tags"] for g in new_g}
    assert "mood" not in by
    assert "治愈" in by["subject"]
    assert "男性" in by["subject"]


def test_reassign_preserves_dict_tag_metadata():
    groups = [
        {
            "id": "mood",
            "tags": [{"name": "治愈", "en": "healing", "icon": "✨"}, "紧张"],
        },
        {"id": "subject", "tags": ["男性"]},
    ]
    ok, new_g, err, moved = reassign_and_remove_group(
        groups, "mood", target_group_id="subject"
    )
    assert ok and not err
    assert "治愈" in moved
    subject = next(g for g in new_g if g["id"] == "subject")
    dict_item = next(
        (t for t in subject["tags"] if isinstance(t, dict) and t.get("name") == "治愈"),
        None,
    )
    assert dict_item is not None
    assert dict_item.get("en") == "healing"
    assert dict_item.get("icon") == "✨"


def test_reject_pool_in_import_classified():
    known = known_group_ids(SAMPLE_GROUPS)
    ok, err = reject_pool_in_classified({"pool": ["x"]}, known)
    assert not ok
    ok, err = reject_pool_in_classified({"": ["x"]}, known)
    assert not ok
    ok, err = reject_pool_in_classified({"mood": ["新词"]}, known)
    assert ok


def test_service_migrate_does_not_clear_video_tags(tmp_path: Path, monkeypatch):
    cfg_path = tmp_path / "tag_config.json"
    cfg_path.write_text(
        json.dumps({"version": "6.0", "tag_groups": SAMPLE_GROUPS}, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr("core.video_organizer_service.TAG_CONFIG_FILE", str(cfg_path))

    svc = VideoOrganizerService(
        settings=SettingsManager.deep_copy_defaults(),
        on_log=lambda _m: None,
        db_path=str(tmp_path / "t.db"),
        results_json=str(tmp_path / "r.json"),
        results_csv=str(tmp_path / "r.csv"),
    )
    # 写入 pool 标准词 + 视频上保留同文字符串
    svc.db.add_tag("pool", "旧池词", is_learned=0)
    svc.db.upsert_video(
        {
            "path": str(tmp_path / "a.mp4"),
            "filename": "a.mp4",
            "tags": ["旧池词", "治愈"],
            "status": "analyzed",
        }
    )
    summary = svc.migrate_pool_standard_tags_to_pending()
    assert "旧池词" in summary["to_pending"] or "旧池词" in summary["removed_from_library"]

    # 标准词表不再有 pool 归属
    details = svc.db.get_tags_detail() or []
    pool_names = [
        t["tag_name"]
        for t in details
        if (t.get("dimension") or "").strip().lower() == "pool"
    ]
    assert "旧池词" not in pool_names

    pend = [p["raw_text"] for p in (svc.db.list_pending_tags(status="pending") or [])]
    assert "旧池词" in pend

    # 视频 tags 字符串保留
    videos = svc.db.get_all_videos()
    assert any("旧池词" in (v.get("tags") or []) for v in videos)


def test_service_approve_standard_requires_group(tmp_path: Path, monkeypatch):
    cfg_path = tmp_path / "tag_config.json"
    cfg_path.write_text(
        json.dumps({"version": "6.0", "tag_groups": SAMPLE_GROUPS}, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr("core.video_organizer_service.TAG_CONFIG_FILE", str(cfg_path))
    svc = VideoOrganizerService(
        settings=SettingsManager.deep_copy_defaults(),
        on_log=lambda _m: None,
        db_path=str(tmp_path / "t.db"),
        results_json=str(tmp_path / "r.json"),
        results_csv=str(tmp_path / "r.csv"),
    )
    svc.db.add_pending_tag("待批词", group_id="", source_path=None)
    rows = svc.db.list_pending_tags(status="pending")
    pid = rows[0]["id"]

    assert svc.resolve_pending_tag(pid, "approve_standard", group_id=None) is False
    assert svc.resolve_pending_tag(pid, "approve_standard", group_id="pool") is False
    assert svc.resolve_pending_tag(pid, "approve_standard", group_id="mood") is True
    names = svc.db.get_tags("mood")
    assert "待批词" in names


def test_service_approve_reassigns_existing_dimension(tmp_path: Path, monkeypatch):
    """同名标准词已在 subject 时，批准到 mood 应改派 dimension。"""
    cfg_path = tmp_path / "tag_config.json"
    cfg_path.write_text(
        json.dumps({"version": "6.0", "tag_groups": SAMPLE_GROUPS}, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr("core.video_organizer_service.TAG_CONFIG_FILE", str(cfg_path))
    svc = VideoOrganizerService(
        settings=SettingsManager.deep_copy_defaults(),
        on_log=lambda _m: None,
        db_path=str(tmp_path / "t.db"),
        results_json=str(tmp_path / "r.json"),
        results_csv=str(tmp_path / "r.csv"),
    )
    svc.db.add_tag("subject", "跨组词", is_learned=0)
    svc.db.add_pending_tag("跨组词", group_id="", source_path=None)
    pid = svc.db.list_pending_tags(status="pending")[0]["id"]
    assert svc.resolve_pending_tag(pid, "approve_standard", group_id="mood") is True
    rows = [t for t in (svc.db.get_tags_detail() or []) if t.get("tag_name") == "跨组词"]
    assert rows
    assert (rows[0].get("dimension") or "").lower() == "mood"
    # 配置中不应仍挂在 subject
    for g in svc.tag_config.get("tag_groups") or []:
        names = []
        for t in g.get("tags") or []:
            names.append(t.get("name") if isinstance(t, dict) else t)
        if g.get("id") == "subject":
            assert "跨组词" not in names
        if g.get("id") == "mood":
            assert "跨组词" in names


def test_reassign_dedupes_duplicate_dimension_rows(tmp_path: Path, monkeypatch):
    """同名已在目标组 + 另一组各有一行时，改派不得 UNIQUE 崩溃。"""
    cfg_path = tmp_path / "tag_config.json"
    cfg_path.write_text(
        json.dumps({"version": "6.0", "tag_groups": SAMPLE_GROUPS}, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr("core.video_organizer_service.TAG_CONFIG_FILE", str(cfg_path))
    svc = VideoOrganizerService(
        settings=SettingsManager.deep_copy_defaults(),
        on_log=lambda _m: None,
        db_path=str(tmp_path / "t.db"),
        results_json=str(tmp_path / "r.json"),
        results_csv=str(tmp_path / "r.csv"),
    )
    # 脏数据：同名两行（历史上 INSERT 进不同 dimension）
    svc.db.add_tag("mood", "重复词", is_learned=0)
    svc.db.add_tag("subject", "重复词", is_learned=0)
    dup = [t for t in (svc.db.get_tags_detail() or []) if t.get("tag_name") == "重复词"]
    assert len(dup) == 2

    svc.db.add_pending_tag("重复词", group_id="", source_path=None)
    pid = svc.db.list_pending_tags(status="pending")[0]["id"]
    assert svc.resolve_pending_tag(pid, "approve_standard", group_id="mood") is True

    left = [t for t in (svc.db.get_tags_detail() or []) if t.get("tag_name") == "重复词"]
    assert len(left) == 1
    assert (left[0].get("dimension") or "").lower() == "mood"


def test_service_delete_tag_group(tmp_path: Path, monkeypatch):
    groups = [
        {"id": "mood", "name": "氛围", "tags": ["治愈"]},
        {"id": "subject", "name": "主体", "tags": ["男性"]},
    ]
    cfg_path = tmp_path / "tag_config.json"
    cfg_path.write_text(
        json.dumps({"version": "6.0", "tag_groups": groups}, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr("core.video_organizer_service.TAG_CONFIG_FILE", str(cfg_path))
    svc = VideoOrganizerService(
        settings=SettingsManager.deep_copy_defaults(),
        on_log=lambda _m: None,
        db_path=str(tmp_path / "t.db"),
        results_json=str(tmp_path / "r.json"),
        results_csv=str(tmp_path / "r.csv"),
    )
    svc.db.add_tag("mood", "治愈", is_learned=0)
    svc.db.add_tag("subject", "男性", is_learned=0)

    bad = svc.delete_tag_group("mood")
    assert bad["ok"] is False

    only = [
        {"id": "mood", "name": "氛围", "tags": []},
    ]
    # 唯一组
    svc.tag_config["tag_groups"] = only
    last = svc.delete_tag_group("mood")
    assert last["ok"] is False

    svc.tag_config["tag_groups"] = [
        {"id": "mood", "name": "氛围", "tags": ["治愈"]},
        {"id": "subject", "name": "主体", "tags": ["男性"]},
    ]
    good = svc.delete_tag_group("mood", target_group_id="subject")
    assert good["ok"] is True
    assert "治愈" in good["moved"]
    # save_tag_config 会 ensure 五组骨架：mood 可能被空壳重建，但词应已改派
    by = {g["id"]: list(g.get("tags") or []) for g in svc.tag_config["tag_groups"]}
    assert "治愈" in by.get("subject", [])
    if "mood" in by:
        assert "治愈" not in by["mood"]
    dim_rows = [t for t in (svc.db.get_tags_detail() or []) if t.get("tag_name") == "治愈"]
    assert dim_rows
    assert (dim_rows[0].get("dimension") or "").lower() == "subject"


def test_import_persist_rejects_pool(tmp_path: Path, monkeypatch):
    cfg_path = tmp_path / "tag_config.json"
    payload = {"version": "6.0", "tag_groups": SAMPLE_GROUPS}
    cfg_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr("core.video_organizer_service.TAG_CONFIG_FILE", str(cfg_path))
    monkeypatch.setattr("core.tag_import_service.TAG_CONFIG_FILE", str(cfg_path))

    class FakeAI:
        def __init__(self):
            self.tag_config = json.loads(cfg_path.read_text(encoding="utf-8"))
            self.settings = SettingsManager.deep_copy_defaults()

    svc = TagImportService(FakeAI())
    ok, err = svc.persist_classified_tags({"pool": ["x"]})
    assert ok is False
    assert err

    ok2, err2 = svc.persist_classified_tags({"mood": ["新导入词"]})
    assert ok2 is True
    disk = json.loads(cfg_path.read_text(encoding="utf-8"))
    mood = next(g for g in disk["tag_groups"] if g["id"] == "mood")
    assert "新导入词" in mood["tags"]