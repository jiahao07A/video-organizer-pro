# -*- coding: utf-8 -*-
"""分析 / XMP 副作用 / Worker 成功语义回归（analysis-tagging-bugs 01–03）。"""
from pathlib import Path

import pytest

from core.video_organizer_service import DEFAULT_SETTINGS, VideoOrganizerService
from gui.workers.analysis_worker import analysis_task_succeeded


@pytest.fixture
def service(tmp_path: Path) -> VideoOrganizerService:
    return VideoOrganizerService(
        settings={**DEFAULT_SETTINGS, "ui_preferences": dict(DEFAULT_SETTINGS.get("ui_preferences", {}))},
        on_log=lambda _m: None,
        db_path=str(tmp_path / "t.db"),
        results_json=str(tmp_path / "r.json"),
        results_csv=str(tmp_path / "r.csv"),
    )


def _touch(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    return str(path.resolve())


def test_sync_metadata_to_xmp_with_tags_no_nameerror(service: VideoOrganizerService, tmp_path: Path):
    """带非空 tags 写 XMP 不得 NameError（person_tags / tag_lookup / id_lookup）。"""
    v = _touch(tmp_path / "clip.mp4")
    service.db.add_tag("subject", "男性", is_learned=0)
    service.db.upsert_video(
        {
            "path": v,
            "filename": "clip.mp4",
            "status": "analyzed",
            "tags": ["男性", "室内", "治愈"],
            "category": "Broll",
            "summary": "摘要",
            "tag_groups": {"subject": ["男性"], "mood": ["治愈"]},
            "face_clusters": [],
        }
    )
    count = service.sync_metadata_to_xmp([v])
    assert count == 1
    xmp = Path(v).with_suffix(".xmp")
    assert xmp.is_file()
    text = xmp.read_text(encoding="utf-8")
    assert "男性" in text or "室内" in text or "治愈" in text


def test_run_analysis_xmp_failure_still_counts_success(service: VideoOrganizerService, tmp_path: Path, monkeypatch):
    """XMP 抛异常不得推翻分析成功计数，也不得冒泡。"""
    v = _touch(tmp_path / "a.mp4")
    service.db.upsert_video({"path": v, "filename": "a.mp4", "status": "pending", "tags": []})

    def fake_process(path, force_reanalyze=False, **_kwargs):
        return {
            "path": path,
            "filename": "a.mp4",
            "status": "analyzed",
            "tags": ["治愈"],
            "category": "Broll",
            "summary": "ok",
            "tag_groups": {},
        }, None

    def boom(*_a, **_k):
        raise NameError("person_tags is not defined")

    monkeypatch.setattr(service, "_process_single_video", fake_process)
    monkeypatch.setattr(service, "sync_metadata_to_xmp", boom)
    monkeypatch.setattr(service, "_save_l3_backup", lambda: None)
    monkeypatch.setattr(service.file_manager, "save_results_to_csv", lambda *_a, **_k: None)

    result = service.run_analysis([v])
    assert result["attempted"] == 1
    assert result["succeeded"] == 1
    assert result["failed"] == 0
    assert result.get("xmp_failed", 0) == 1
    row = service.db.get_video_by_path(v)
    assert row["status"] == "analyzed"


def test_run_analysis_failure_reason_in_message(service: VideoOrganizerService, tmp_path: Path, monkeypatch):
    v = _touch(tmp_path / "bad.mp4")
    service.db.upsert_video({"path": v, "filename": "bad.mp4", "status": "pending", "tags": []})

    def fake_process(path, force_reanalyze=False, **_kwargs):
        return None, "无法抽取视频帧"

    monkeypatch.setattr(service, "_process_single_video", fake_process)
    monkeypatch.setattr(service, "sync_metadata_to_xmp", lambda *_a, **_k: None)
    monkeypatch.setattr(service, "_save_l3_backup", lambda: None)
    monkeypatch.setattr(service.file_manager, "save_results_to_csv", lambda *_a, **_k: None)

    result = service.run_analysis([v])
    assert result["succeeded"] == 0
    assert result["failed"] == 1
    assert "无法抽取视频帧" in result["message"]
    row = service.db.get_video_by_path(v)
    assert row["status"] == "failed"
    meta = row.get("raw_metadata") or {}
    assert meta.get("last_error") == "无法抽取视频帧"


@pytest.mark.parametrize(
    "payload,expected",
    [
        ({"attempted": 0, "succeeded": 0, "failed": 0}, True),
        ({"attempted": 2, "succeeded": 2, "failed": 0}, True),
        ({"attempted": 2, "succeeded": 0, "failed": 2}, False),
        ({"attempted": 2, "succeeded": 1, "failed": 1}, False),
    ],
)
def test_analysis_task_succeeded_semantics(payload, expected):
    assert analysis_task_succeeded(payload) is expected


def test_sync_xmp_path_key_normalizes(service: VideoOrganizerService, tmp_path: Path):
    """路径形态差异仍应命中（Windows 下常见）。"""
    v = _touch(tmp_path / "norm.mp4")
    service.db.upsert_video(
        {
            "path": v,
            "filename": "norm.mp4",
            "status": "analyzed",
            "tags": ["场景"],
            "summary": "s",
        }
    )
    # 故意传入另一形态：若未规范化可能漏选；规范化后应写 1 个
    alt = v.replace("\\", "/") if "\\" in v else v
    count = service.sync_metadata_to_xmp([alt])
    assert count == 1
    assert Path(v).with_suffix(".xmp").is_file()