# -*- coding: utf-8 -*-
"""post-review-hardening: 01–03, 07–09 服务层回归。"""
from pathlib import Path

import pytest

from core.video_organizer_service import (
    DEFAULT_SETTINGS,
    VideoOrganizerService,
    normalize_work_path,
)


@pytest.fixture
def service(tmp_path: Path) -> VideoOrganizerService:
    return VideoOrganizerService(
        settings={
            **DEFAULT_SETTINGS,
            "ui_preferences": dict(DEFAULT_SETTINGS.get("ui_preferences", {})),
            "processing": dict(DEFAULT_SETTINGS.get("processing", {})),
        },
        on_log=lambda _m: None,
        db_path=str(tmp_path / "t.db"),
        results_json=str(tmp_path / "r.json"),
        results_csv=str(tmp_path / "r.csv"),
    )


def _touch(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    return str(path.resolve())


def test_vocab_cache_fingerprint_changes_with_synonym(service: VideoOrganizerService):
    fp1 = service.ai.build_vocab_cache_fingerprint()
    service.db.add_synonym("男性", "男人")
    fp2 = service.ai.build_vocab_cache_fingerprint()
    assert fp1 != fp2


def test_analyze_video_cache_miss_after_vocab_change(service: VideoOrganizerService, monkeypatch):
    """词表变更后缓存键变化，不再命中旧条目。"""
    calls = {"n": 0}

    def fake_api(*_a, **_k):
        calls["n"] += 1
        return {"tags": ["治愈"], "category": "Broll", "summary": "s", "tag_groups": {}}

    monkeypatch.setattr(service.ai, "_get_api_response", fake_api)
    monkeypatch.setattr(service.ai, "build_analysis_prompts", lambda: {
        "system_prompt": "sys", "user_prompt": "user",
    })
    monkeypatch.setattr(
        service.ai, "apply_tag_normalization",
        lambda data, **kw: data,
    )
    monkeypatch.setattr(
        service.ai, "resolve_task_route",
        lambda *_a, **_k: {"model": "m1"},
    )

    frames = ["frame"]
    fp = service.ai.build_vocab_cache_fingerprint()
    key1 = f"hash_m1_{fp}"
    r1 = service.ai.analyze_video(frames, cache_key=key1, use_cache=True)
    assert r1 is not None
    assert calls["n"] == 1

    # 同键再读走缓存
    r2 = service.ai.analyze_video(frames, cache_key=key1, use_cache=True)
    assert r2 is not None
    assert calls["n"] == 1

    # 改别名 → 新指纹 → 新键 → 再调 API
    service.db.add_synonym("标准A", "别名A")
    fp2 = service.ai.build_vocab_cache_fingerprint()
    assert fp2 != fp
    key2 = f"hash_m1_{fp2}"
    r3 = service.ai.analyze_video(frames, cache_key=key2, use_cache=True)
    assert r3 is not None
    assert calls["n"] == 2


def test_force_reanalyze_still_use_cache_false(service: VideoOrganizerService, tmp_path: Path, monkeypatch):
    v = _touch(tmp_path / "c.mp4")
    service.db.upsert_video({
        "path": v, "filename": "c.mp4", "status": "analyzed",
        "tags": ["旧"], "category": "旧类", "summary": "旧摘要",
    })
    calls = {"use_cache": []}

    def fake_analyze(frames, cache_key=None, use_cache=True):
        calls["use_cache"].append(use_cache)
        return {
            "tags": ["新词"], "category": "新类", "summary": "新摘要", "tag_groups": {},
        }

    monkeypatch.setattr(service.ai, "analyze_video", fake_analyze)
    monkeypatch.setattr(
        service.processor, "extract_frames",
        lambda *a, **k: {"frames": ["x"], "thumbnail": None, "phash": ""},
    )
    monkeypatch.setattr(service.processor, "get_file_hash", lambda p: "hash")
    monkeypatch.setattr(service, "sync_metadata_to_xmp", lambda *_a, **_k: None)
    monkeypatch.setattr(service, "_save_l3_backup", lambda: None)
    monkeypatch.setattr(service.file_manager, "save_results_to_csv", lambda *_a, **_k: None)
    monkeypatch.setattr(service.tag_processor, "process", lambda tags, cat: (list(tags or []), cat))
    monkeypatch.setattr(service.ai, "apply_tag_normalization", lambda data, **kw: data)

    r2 = service.run_analysis([v], force_reanalyze=True)
    assert r2["succeeded"] == 1
    assert calls["use_cache"] == [False]


def test_export_ale_path_normalize_matches_fcpx(service: VideoOrganizerService, tmp_path: Path):
    p1 = _touch(tmp_path / "a.mp4")
    p2 = _touch(tmp_path / "b.mp4")
    service.db.upsert_video({
        "path": p1, "filename": "a.mp4", "status": "analyzed",
        "tags": ["t"], "category": "C", "summary": "s",
    })
    service.db.upsert_video({
        "path": p2, "filename": "b.mp4", "status": "analyzed",
        "tags": ["t2"], "category": "C", "summary": "s2",
    })
    out_ale = tmp_path / "out.ale"
    out_fcpx = tmp_path / "out.fcpxml"
    # 故意用不同路径形态
    alt = str(Path(p1))  # same resolve ideally
    ok = service.export_to_ale(str(out_ale), [alt])
    assert ok is True
    text = out_ale.read_text(encoding="utf-8-sig")
    assert "a.mp4" in text
    assert "b.mp4" not in text

    service.export_to_fcpx_xml(str(out_fcpx), [alt])
    fcpx = out_fcpx.read_text(encoding="utf-8")
    assert "a.mp4" in fcpx
    assert "b.mp4" not in fcpx

    # 空列表 → 导出 0 条（不误当全库）
    out_empty = tmp_path / "empty.ale"
    ok2 = service.export_to_ale(str(out_empty), [])
    assert ok2 is False


def test_analysis_failure_clears_memory_cache(service: VideoOrganizerService, tmp_path: Path):
    v = _touch(tmp_path / "f.mp4")
    row = {
        "path": v, "filename": "f.mp4", "status": "analyzed",
        "tags": ["ok"], "category": "C", "summary": "s",
    }
    service.db.upsert_video(row)
    with service._cache_lock:
        service._memory_cache = {v: dict(row)}
    service._persist_analysis_failure(v, "boom")
    videos = service.get_all_videos()
    found = [x for x in videos if normalize_work_path(x.get("path", "")) == normalize_work_path(v)]
    assert found
    assert found[0].get("status") == "failed"


def test_bulk_replace_respects_selected_paths(service: VideoOrganizerService, tmp_path: Path):
    a = _touch(tmp_path / "a.mp4")
    b = _touch(tmp_path / "b.mp4")
    service.db.upsert_video({
        "path": a, "filename": "a.mp4", "status": "analyzed", "tags": ["旧词"],
    })
    service.db.upsert_video({
        "path": b, "filename": "b.mp4", "status": "analyzed", "tags": ["旧词"],
    })
    service.bulk_replace_tags("旧词", "新词", selected_paths=[a])
    ra = service.db.get_video_by_path(a)
    rb = service.db.get_video_by_path(b)
    assert "新词" in (ra.get("tags") or [])
    assert "旧词" in (rb.get("tags") or [])


def test_persist_success_default_no_auto_xmp(service: VideoOrganizerService, tmp_path: Path, monkeypatch):
    v = _touch(tmp_path / "x.mp4")
    called = {"n": 0}

    def boom(*_a, **_k):
        called["n"] += 1
        return 1

    monkeypatch.setattr(service, "sync_metadata_to_xmp", boom)
    res = {
        "path": v, "filename": "x.mp4", "status": "analyzed",
        "tags": [], "category": "C", "summary": "s",
    }
    # 显式默认关（避免共享 settings 污染）
    service.settings.setdefault("processing", {})["auto_sync_xmp_after_analysis"] = False
    xmp_fail = service._persist_analysis_success(res)
    assert xmp_fail == 0
    assert called["n"] == 0

    service.settings["processing"]["auto_sync_xmp_after_analysis"] = True
    xmp_fail2 = service._persist_analysis_success(res)
    assert called["n"] == 1
    assert xmp_fail2 == 0


def test_plan_physical_migration_no_disk_move(service: VideoOrganizerService, tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    f = src / "clip.mp4"
    f.write_bytes(b"data")
    path = str(f.resolve())
    dest_root = tmp_path / "dest"
    dest_root.mkdir()
    service.db.upsert_video({
        "path": path, "filename": "clip.mp4", "status": "analyzed",
        "category": "Broll", "tags": [],
    })
    plan = service.plan_physical_migration(str(dest_root), [path])
    assert len(plan) == 1
    assert plan[0]["old_path"] == path
    assert "Broll" in plan[0]["new_path"]
    # 未执行：源仍在，目标不存在
    assert f.exists()
    assert not Path(plan[0]["new_path"]).exists()
    assert plan[0]["source_path"] == path
    assert plan[0]["target_path"] == plan[0]["new_path"]


def test_physical_migration_preview_reports_conflict_without_overwrite(
    service: VideoOrganizerService, tmp_path: Path
):
    source = _touch(tmp_path / "source" / "clip.mp4")
    target_root = tmp_path / "organized"
    existing = target_root / "Broll" / "clip.mp4"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"existing")
    service.db.upsert_video({
        "path": source, "filename": "clip.mp4", "status": "analyzed",
        "category": "Broll", "tags": [],
    })

    plan = service.plan_physical_migration(str(target_root), [source])

    assert len(plan) == 1
    assert plan[0]["source_path"] == source
    assert plan[0]["requested_target_path"] == str(existing)
    assert plan[0]["target_path"].endswith("clip_1.mp4")
    assert plan[0]["target_conflict"] is True
    assert any(flag.startswith("目标已存在") for flag in plan[0]["risk_flags"])
    assert plan[0]["status"] == "review"
    assert existing.read_bytes() == b"existing"


def test_physical_migration_preview_reports_external_change(
    service: VideoOrganizerService, tmp_path: Path, monkeypatch
):
    source = _touch(tmp_path / "source" / "clip.mp4")
    service.db.upsert_video({
        "path": source, "filename": "clip.mp4", "file_hash": "old-hash",
        "status": "analyzed", "category": "Broll", "tags": [],
    })
    monkeypatch.setattr(service.processor, "get_file_hash", lambda _path: "new-hash")

    plan = service.plan_physical_migration(str(tmp_path / "organized"), [source])

    assert plan[0]["external_change"] is False
    assert "源文件可能已被外部修改" in plan[0]["risk_flags"]
    assert plan[0]["status"] == "blocked"


def test_physical_migration_unsafe_category_stays_under_target_root(service, tmp_path):
    source = _touch(tmp_path / "source" / "clip.mp4")
    target_root = tmp_path / "organized"
    service.db.upsert_video({
        "path": source, "filename": "clip.mp4", "status": "analyzed",
        "category": "..\\outside", "tags": [],
    })
    plan = service.plan_physical_migration(str(target_root), [source])
    assert len(plan) == 1
    assert plan[0]["category"] == "Other"
    assert Path(plan[0]["target_path"]).parent == target_root / "Other"
    assert any("安全目录 Other" in flag for flag in plan[0]["risk_flags"])


def test_physical_migration_database_failure_restores_source_and_cleans_target(
    service, tmp_path, monkeypatch
):
    source = _touch(tmp_path / "source" / "clip.mp4")
    target_root = tmp_path / "organized"
    service.db.upsert_video({
        "path": source, "filename": "clip.mp4", "status": "analyzed",
        "category": "Broll", "tags": [],
    })
    monkeypatch.setattr(
        service.db,
        "move_video_record",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("db unavailable")),
    )
    result = service.execute_physical_migration(str(target_root), [source], return_details=True)
    assert result["failed_count"] == 1
    assert result["success_count"] == 0
    assert Path(source).exists()
    assert not list(target_root.rglob("clip.mp4"))
    assert service.db.get_video_by_path(source) is not None


def test_physical_migration_rejects_forged_plan_source(service, tmp_path):
    source = _touch(tmp_path / "source" / "clip.mp4")
    outside = _touch(tmp_path / "outside.mp4")
    target_root = tmp_path / "organized"
    service.db.upsert_video({
        "path": source, "filename": "clip.mp4", "status": "analyzed",
        "category": "Broll", "tags": [],
    })
    forged = {
        "old_path": outside,
        "new_path": str(target_root / "Broll" / "outside.mp4"),
        "source_path": outside,
        "target_path": str(target_root / "Broll" / "outside.mp4"),
        "category": "Broll",
        "filename": "outside.mp4",
        "status": "ready",
        "risk_flags": [],
        "source_size": outside and Path(outside).stat().st_size,
        "source_mtime_ns": Path(outside).stat().st_mtime_ns,
    }
    result = service.execute_physical_migration(
        str(target_root), [source], plan=[forged], return_details=True
    )
    assert result["blocked_count"] == 1
    assert result["success_count"] == 0
    assert Path(outside).exists()
    assert not Path(forged["new_path"]).exists()


def test_physical_migration_forged_plan_with_empty_scope_is_blocked(service, tmp_path):
    source = _touch(tmp_path / "source" / "clip.mp4")
    target_root = tmp_path / "organized"
    service.db.upsert_video({
        "path": source, "filename": "clip.mp4", "status": "analyzed",
        "category": "Broll", "tags": [],
    })
    forged = {
        "old_path": source,
        "new_path": str(target_root / "Broll" / "clip.mp4"),
        "source_path": source,
        "target_path": str(target_root / "Broll" / "clip.mp4"),
        "category": "Broll",
        "filename": "clip.mp4",
        "status": "ready",
        "risk_flags": [],
        "source_size": Path(source).stat().st_size,
        "source_mtime_ns": Path(source).stat().st_mtime_ns,
    }
    result = service.execute_physical_migration(
        str(target_root), [], plan=[forged], return_details=True
    )
    assert result["blocked_count"] == 1
    assert result["success_count"] == 0
    assert Path(source).exists()


def test_rename_xmp_failure_is_reported_and_video_path_is_compensated(
    service, tmp_path, monkeypatch
):
    source = _touch(tmp_path / "clip.mp4")
    source_xmp = Path(source).with_suffix(".xmp")
    source_xmp.write_text("xmp", encoding="utf-8")
    service.db.upsert_video({
        "path": source, "filename": "clip.mp4", "status": "analyzed",
        "tags": [], "category": "Broll", "summary": "",
    })
    original_rename = __import__("os").rename

    def fail_sidecar(src, dst):
        if str(src).endswith(".xmp"):
            raise OSError("sidecar locked")
        return original_rename(src, dst)

    monkeypatch.setattr("core.video_organizer_service.os.rename", fail_sidecar)
    service.batch_rename(
        dry_run=False,
        selected_paths=[source],
        custom_pattern="renamed-{original_name}",
        date_value="20261017",
    )
    assert Path(source).exists()
    assert source_xmp.exists()
    assert service._last_rename_result["failed_count"] == 1
    assert service.db.get_video_by_path(source) is not None


def test_automatic_xmp_exception_is_reported_with_path(service, tmp_path, monkeypatch):
    source = _touch(tmp_path / "clip.mp4")
    row = {
        "path": source, "filename": "clip.mp4", "status": "analyzed",
        "tags": [], "category": "Broll", "summary": "s",
    }
    monkeypatch.setattr(
        service,
        "sync_metadata_to_xmp",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )
    service.settings["processing"]["auto_sync_xmp_after_analysis"] = True
    failures = []
    assert service._persist_analysis_success(row, xmp_failure_sink=failures) == 1
    assert failures == [{"path": source, "reason": "disk full"}]


def test_rename_missing_physical_file_is_reported_as_failure(service, tmp_path):
    source = str(tmp_path / "missing.mp4")
    service.db.upsert_video({
        "path": source, "filename": "missing.mp4", "status": "analyzed",
        "tags": [], "category": "Broll", "summary": "",
    })
    service.batch_rename(
        dry_run=False,
        selected_paths=[source],
        custom_pattern="renamed-{original_name}",
        date_value="20261017",
    )
    assert service._last_rename_result["failed_count"] == 1
    assert "未找到物理文件" in service._last_rename_result["failed_items"][0]["reason"]


def test_rename_database_failure_restores_physical_source(service, tmp_path, monkeypatch):
    source = _touch(tmp_path / "clip.mp4")
    service.db.upsert_video({
        "path": source, "filename": "clip.mp4", "status": "analyzed",
        "tags": [], "category": "Broll", "summary": "",
    })
    monkeypatch.setattr(
        service.db,
        "add_rename_record",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("db unavailable")),
    )
    service.batch_rename(
        dry_run=False,
        selected_paths=[source],
        custom_pattern="renamed-{original_name}",
        date_value="20261017",
    )
    assert Path(source).exists()
    assert not Path(source).with_name("renamed-clip.mp4").exists()
    assert service._last_rename_result["failed_count"] == 1
    assert service.db.get_video_by_path(source) is not None
