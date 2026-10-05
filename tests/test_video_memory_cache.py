# -*- coding: utf-8 -*-
"""L1 缓存：禁止分析过程中的零散写入冒充全库。"""
from pathlib import Path

import pytest

from core.video_organizer_service import DEFAULT_SETTINGS, VideoOrganizerService


@pytest.fixture
def service(tmp_path: Path) -> VideoOrganizerService:
    return VideoOrganizerService(
        settings={**DEFAULT_SETTINGS, "ui_preferences": {}},
        on_log=lambda _m: None,
        db_path=str(tmp_path / "t.db"),
        results_json=str(tmp_path / "r.json"),
        results_csv=str(tmp_path / "r.csv"),
    )


def _row(path: str, status: str, **extra):
    d = {
        "path": path,
        "filename": Path(path).name,
        "status": status,
        "tags": extra.get("tags", ["a"]),
        "summary": extra.get("summary", "s"),
        "category": "Broll",
        "tag_groups": {},
    }
    d.update(extra)
    return d


def test_partial_cache_put_does_not_hide_library(service: VideoOrganizerService, tmp_path: Path):
    p1 = str((tmp_path / "a.mp4").resolve())
    p2 = str((tmp_path / "b.mp4").resolve())
    (tmp_path / "a.mp4").write_bytes(b"x")
    (tmp_path / "b.mp4").write_bytes(b"y")
    service.db.upsert_video(_row(p1, "pending", tags=[], summary=""))
    service.db.upsert_video(_row(p2, "pending", tags=[], summary=""))

    all1 = service.get_all_videos()
    assert len(all1) == 2

    # 模拟分析失败写入：不得把全库变成 1 条 failed
    service._persist_analysis_failure(p1, "boom")
    all2 = service.get_all_videos()
    assert len(all2) == 2
    by = {v["path"]: v for v in all2}
    # 路径可能规范化，按 basename 查
    statuses = {Path(v["path"]).name: v["status"] for v in all2}
    assert statuses["a.mp4"] == "failed"
    assert statuses["b.mp4"] == "pending"

    # 成功覆盖失败
    ok = _row(p1, "analyzed", tags=["治愈"], summary="好")
    service._persist_analysis_success(ok)
    all3 = service.get_all_videos()
    assert len(all3) == 2
    statuses = {Path(v["path"]).name: v["status"] for v in all3}
    assert statuses["a.mp4"] == "analyzed"


def test_invalidate_forces_db_reload(service: VideoOrganizerService, tmp_path: Path):
    p1 = str((tmp_path / "c.mp4").resolve())
    (tmp_path / "c.mp4").write_bytes(b"z")
    service.db.upsert_video(_row(p1, "pending", tags=[], summary=""))
    service.get_all_videos()
    service.db.upsert_video(_row(p1, "analyzed", tags=["t"], summary="s"))
    # 缓存仍可能是旧的，直到 invalidate
    service.invalidate_video_memory_cache()
    v = service.get_all_videos()[0]
    assert v["status"] == "analyzed"