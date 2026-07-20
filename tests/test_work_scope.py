# -*- coding: utf-8 -*-
"""工作范围服务层行为测试（ticket 02）。"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from core.video_organizer_service import (
    DEFAULT_SETTINGS,
    VideoOrganizerService,
    dedupe_scope_paths,
    is_video_path_in_scope,
    normalize_work_path,
)


@pytest.fixture
def service(tmp_path: Path) -> VideoOrganizerService:
    """隔离 DB / 结果文件，避免污染本机库。"""
    db_path = str(tmp_path / "work_scope_test.db")
    json_path = str(tmp_path / "results.json")
    csv_path = str(tmp_path / "results.csv")
    settings = {
        **DEFAULT_SETTINGS,
        "ui_preferences": dict(DEFAULT_SETTINGS.get("ui_preferences", {})),
    }
    return VideoOrganizerService(
        settings=settings,
        on_log=lambda _m: None,
        db_path=db_path,
        results_json=json_path,
        results_csv=csv_path,
    )


def _touch_video(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")  # 扫盘只认扩展名，不读内容
    return str(path.resolve())


class TestPathMembership:
    def test_file_exact_match(self, tmp_path: Path):
        video = _touch_video(tmp_path / "a.mp4")
        other = _touch_video(tmp_path / "b.mp4")
        assert is_video_path_in_scope(video, [video]) is True
        assert is_video_path_in_scope(other, [video]) is False

    def test_folder_includes_nested(self, tmp_path: Path):
        root = tmp_path / "clips"
        nested = _touch_video(root / "sub" / "deep" / "x.mp4")
        outside = _touch_video(tmp_path / "outside.mp4")
        scope = [str(root.resolve())]
        assert is_video_path_in_scope(nested, scope) is True
        assert is_video_path_in_scope(outside, scope) is False

    def test_empty_scope_rejects_all(self, tmp_path: Path):
        video = _touch_video(tmp_path / "a.mp4")
        assert is_video_path_in_scope(video, []) is False


class TestReplaceWorkScope:
    def test_replace_overwrites_entire_scope(self, service: VideoOrganizerService, tmp_path: Path):
        p1 = str((tmp_path / "f1").resolve())
        p2 = str((tmp_path / "f2").resolve())
        p3 = str((tmp_path / "f3").resolve())
        (tmp_path / "f1").mkdir()
        (tmp_path / "f2").mkdir()
        (tmp_path / "f3").mkdir()

        service.set_work_scope_paths([p1, p2], scan=False)
        assert set(map(normalize_work_path, service.get_work_scope_paths())) == {
            normalize_work_path(p1),
            normalize_work_path(p2),
        }

        service.replace_work_scope([p3], scan=False)
        paths = service.get_work_scope_paths()
        assert len(paths) == 1
        assert normalize_work_path(paths[0]) == normalize_work_path(p3)

    def test_dedupe_on_replace(self, service: VideoOrganizerService, tmp_path: Path):
        d = tmp_path / "same"
        d.mkdir()
        p = str(d.resolve())
        service.set_work_scope_paths([p, p, str(d)], scan=False)
        assert len(service.get_work_scope_paths()) == 1


class TestListInScope:
    def test_empty_scope_lists_empty_even_if_db_has_videos(
        self, service: VideoOrganizerService, tmp_path: Path
    ):
        video = _touch_video(tmp_path / "lib" / "only_in_db.mp4")
        service.db.insert_video_if_absent(
            {
                "path": video,
                "filename": "only_in_db.mp4",
                "status": "analyzed",
                "category": "Broll",
                "summary": "已有摘要",
                "tags": ["t1"],
            }
        )
        assert service.get_work_scope_paths() == []
        assert service.get_videos_in_work_scope() == []
        # 全库仍有记录
        assert len(service.get_all_videos()) == 1

    def test_list_only_in_scope(self, service: VideoOrganizerService, tmp_path: Path):
        in_dir = tmp_path / "in"
        out_dir = tmp_path / "out"
        v_in = _touch_video(in_dir / "in.mp4")
        v_out = _touch_video(out_dir / "out.mp4")
        for p, name in ((v_in, "in.mp4"), (v_out, "out.mp4")):
            service.db.insert_video_if_absent(
                {"path": p, "filename": name, "status": "pending", "tags": []}
            )
        service.set_work_scope_paths([str(in_dir.resolve())], scan=False)
        listed = service.get_videos_in_work_scope()
        paths = {normalize_work_path(v["path"]) for v in listed}
        assert paths == {normalize_work_path(v_in)}


class TestScanAndRegister:
    def test_scan_registers_new_as_pending(self, service: VideoOrganizerService, tmp_path: Path):
        folder = tmp_path / "batch"
        v1 = _touch_video(folder / "a.mp4")
        v2 = _touch_video(folder / "sub" / "b.mov")
        result = service.replace_work_scope([str(folder.resolve())], scan=True)
        registered = {normalize_work_path(p) for p in result["registered"]}
        assert normalize_work_path(v1) in registered
        assert normalize_work_path(v2) in registered

        rows = service.get_videos_in_work_scope()
        assert len(rows) == 2
        for row in rows:
            assert row.get("status") == "pending"

    def test_scan_does_not_overwrite_existing_analysis(
        self, service: VideoOrganizerService, tmp_path: Path
    ):
        folder = tmp_path / "keep"
        video = _touch_video(folder / "keep.mp4")
        service.db.insert_video_if_absent(
            {
                "path": video,
                "filename": "keep.mp4",
                "status": "analyzed",
                "category": "Aroll",
                "summary": "原始摘要勿覆盖",
                "tags": ["保留"],
                "emotion": "平静",
            }
        )
        result = service.replace_work_scope([str(folder.resolve())], scan=True)
        assert result["registered"] == []

        row = service.db.get_video_by_path(video)
        assert row is not None
        assert row["status"] == "analyzed"
        assert row["category"] == "Aroll"
        assert row["summary"] == "原始摘要勿覆盖"
        assert row["tags"] == ["保留"]
        assert row["emotion"] == "平静"

        # 再次扫盘仍不覆盖
        service.scan_and_register_work_scope()
        row2 = service.db.get_video_by_path(video)
        assert row2["summary"] == "原始摘要勿覆盖"

    def test_file_scope_registers_single(self, service: VideoOrganizerService, tmp_path: Path):
        video = _touch_video(tmp_path / "single.mp4")
        other = _touch_video(tmp_path / "other.mp4")
        result = service.replace_work_scope([video], scan=True)
        assert len(result["registered"]) == 1
        assert normalize_work_path(result["registered"][0]) == normalize_work_path(video)
        listed = service.get_videos_in_work_scope()
        assert len(listed) == 1
        assert normalize_work_path(listed[0]["path"]) == normalize_work_path(video)
        assert service.is_path_in_work_scope(other) is False


class TestDedupeHelper:
    def test_dedupe_preserves_order(self, tmp_path: Path):
        a = str((tmp_path / "a").resolve())
        b = str((tmp_path / "b").resolve())
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        out = dedupe_scope_paths([a, b, a])
        assert [normalize_work_path(x) for x in out] == [
            normalize_work_path(a),
            normalize_work_path(b),
        ]