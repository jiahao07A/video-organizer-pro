# -*- coding: utf-8 -*-
"""操作边界 + 累加 + 筛选（ticket 03–06）。"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.video_organizer_service import DEFAULT_SETTINGS, VideoOrganizerService, normalize_work_path
from gui.models.proxy_model import video_matches_filter


@pytest.fixture
def service(tmp_path: Path) -> VideoOrganizerService:
    return VideoOrganizerService(
        settings={**DEFAULT_SETTINGS, "ui_preferences": dict(DEFAULT_SETTINGS.get("ui_preferences", {}))},
        on_log=lambda _m: None,
        db_path=str(tmp_path / "ops.db"),
        results_json=str(tmp_path / "r.json"),
        results_csv=str(tmp_path / "r.csv"),
    )


def _reg(service, path: Path, status="pending", **extra):
    path.write_bytes(b"")
    abs_p = str(path.resolve())
    service.db.upsert_video(
        {
            "path": abs_p,
            "filename": path.name,
            "status": status,
            "tags": extra.get("tags", []),
            "category": extra.get("category"),
            "summary": extra.get("summary"),
        }
    )
    return abs_p


def test_resolve_empty_scope(service, tmp_path):
    assert service.resolve_operation_target_paths() == []
    assert service.resolve_operation_target_paths(["x"]) == []


def test_resolve_unchecked_is_scope_not_library(service, tmp_path):
    a = _reg(service, tmp_path / "a.mp4", status="analyzed")
    b = _reg(service, tmp_path / "b.mp4", status="analyzed")
    service.replace_work_scope([a], scan=False)
    targets = service.resolve_operation_target_paths(None)
    assert set(map(normalize_work_path, targets)) == {normalize_work_path(a)}
    assert normalize_work_path(b) not in set(map(normalize_work_path, targets))


def test_resolve_checked_intersection(service, tmp_path):
    a = _reg(service, tmp_path / "a.mp4")
    b = _reg(service, tmp_path / "b.mp4")
    service.replace_work_scope([a, b], scan=False)
    targets = service.resolve_operation_target_paths([a])
    assert set(map(normalize_work_path, targets)) == {normalize_work_path(a)}


def test_append_work_scope(service, tmp_path):
    p1 = str((tmp_path / "f1").resolve())
    p2 = str((tmp_path / "f2").resolve())
    Path(p1).mkdir()
    Path(p2).mkdir()
    service.replace_work_scope([p1], scan=False)
    service.append_work_scope([p2], scan=False)
    paths = set(map(normalize_work_path, service.get_work_scope_paths()))
    assert normalize_work_path(p1) in paths
    assert normalize_work_path(p2) in paths


def test_filter_status_and_ignores_emotions_param():
    """分析状态可筛；emotions 维已拆除（传入亦忽略，不依赖 emotion 字段）。"""
    v = {"filename": "x.mp4", "status": "pending", "tags": ["a", "治愈"], "summary": ""}
    # 旧 emotions 参数不再生效，不应因缺失 emotion 字段而误杀
    assert video_matches_filter(v, {"emotions": ["治愈"]})
    assert video_matches_filter(v, {"emotions": ["悲伤"]})
    assert video_matches_filter(v, {"analysis_statuses": ["未分析"]})
    assert not video_matches_filter(v, {"analysis_statuses": ["已分析"]})
    assert video_matches_filter(v, {"analysis_statuses": ["未命名"]})
    assert not video_matches_filter(v, {"analysis_statuses": ["已重命名"]})
    renamed = {"filename": "y.mp4", "status": "renamed", "tags": [], "summary": ""}
    assert video_matches_filter(renamed, {"analysis_statuses": ["已重命名"]})
    assert not video_matches_filter(renamed, {"analysis_statuses": ["未命名"]})


def test_filter_tag_all_and_summary():
    v = {"tags": ["a", "b"], "summary": "有内容", "status": "analyzed"}
    assert video_matches_filter(v, {"tags": ["a", "b"], "tag_match_mode": "all"})
    assert not video_matches_filter(v, {"tags": ["a", "c"], "tag_match_mode": "all"})
    assert video_matches_filter(v, {"tags": ["a", "c"], "tag_match_mode": "any"})
    assert video_matches_filter(v, {"summary_empty": "nonempty"})
    assert not video_matches_filter(v, {"summary_empty": "empty"})
    v2 = {"tags": [], "summary": "  ", "status": "analyzed"}
    assert video_matches_filter(v2, {"summary_empty": "empty"})


def test_filter_mood_via_tags_any_all():
    """基调通过标签（含氛围标准词）收窄，any/all 不回归。"""
    v = {"tags": ["紧张", "纪实", "室内"], "summary": "s", "status": "analyzed"}
    assert video_matches_filter(v, {"tags": ["紧张"], "tag_match_mode": "any"})
    assert video_matches_filter(v, {"tags": ["紧张", "纪实"], "tag_match_mode": "all"})
    assert not video_matches_filter(v, {"tags": ["紧张", "治愈"], "tag_match_mode": "all"})
    assert video_matches_filter(v, {"tags": ["紧张", "治愈"], "tag_match_mode": "any"})
