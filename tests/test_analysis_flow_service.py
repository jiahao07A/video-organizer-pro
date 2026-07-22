# -*- coding: utf-8 -*-
"""服务层：分析目标集 + 移出范围 / 取消入库（ticket 01/03）。"""
from pathlib import Path

import pytest

from core.video_organizer_service import DEFAULT_SETTINGS, VideoOrganizerService, normalize_work_path


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


def test_run_analysis_does_not_skip_pending_in_db(service: VideoOrganizerService, tmp_path: Path, monkeypatch):
    """在库 pending 必须进入分析，不能因「已在库」秒完成。"""
    v = _touch(tmp_path / "a.mp4")
    service.db.upsert_video(
        {"path": v, "filename": "a.mp4", "status": "pending", "tags": [], "category": None}
    )

    calls = []

    def fake_process(path, force_reanalyze=False, **_kwargs):
        calls.append(path)
        return {
            "path": path,
            "filename": "a.mp4",
            "status": "analyzed",
            "tags": ["治愈"],
            "category": "Broll",
            "summary": "test",
            "tag_groups": {},
        }, None

    monkeypatch.setattr(service, "_process_single_video", fake_process)
    monkeypatch.setattr(service, "sync_metadata_to_xmp", lambda *_a, **_k: None)
    monkeypatch.setattr(service, "_save_l3_backup", lambda: None)
    monkeypatch.setattr(service.file_manager, "save_results_to_csv", lambda *_a, **_k: None)

    result = service.run_analysis([v], force_reanalyze=False)
    assert result["attempted"] == 1
    assert result["succeeded"] == 1
    assert calls == [v]
    row = service.db.get_video_by_path(v)
    assert row["status"] == "analyzed"


def test_run_analysis_skips_analyzed_unless_force(service: VideoOrganizerService, tmp_path: Path, monkeypatch):
    v = _touch(tmp_path / "b.mp4")
    service.db.upsert_video(
        {
            "path": v,
            "filename": "b.mp4",
            "status": "analyzed",
            "tags": ["旧"],
            "category": "旧类",
            "summary": "旧摘要",
        }
    )
    calls = []

    def fake_process(path, force_reanalyze=False, **_kwargs):
        calls.append(path)
        return {
            "path": path,
            "filename": "b.mp4",
            "status": "analyzed",
            "tags": ["新"],
            "category": "新类",
            "summary": "新摘要",
            "tag_groups": {},
        }, None

    monkeypatch.setattr(service, "_process_single_video", fake_process)
    monkeypatch.setattr(service, "sync_metadata_to_xmp", lambda *_a, **_k: None)
    monkeypatch.setattr(service, "_save_l3_backup", lambda: None)
    monkeypatch.setattr(service.file_manager, "save_results_to_csv", lambda *_a, **_k: None)

    r1 = service.run_analysis([v], force_reanalyze=False)
    assert r1["attempted"] == 0
    assert calls == []

    r2 = service.run_analysis([v], force_reanalyze=True)
    assert r2["attempted"] == 1
    assert calls == [v]
    row = service.db.get_video_by_path(v)
    assert row["tags"] == ["新"]
    assert row["summary"] == "新摘要"


def test_remove_from_scope_keeps_library_and_file(service: VideoOrganizerService, tmp_path: Path):
    root = tmp_path / "clips"
    v = _touch(root / "x.mp4")
    service.set_work_scope_paths([str(root.resolve())], scan=True)
    assert any(x.get("path") == v for x in service.get_videos_in_work_scope())
    service.remove_videos_from_work_scope([v])
    assert not any(x.get("path") == v for x in service.get_videos_in_work_scope())
    # 库中仍在
    assert service.db.get_video_by_path(v) is not None
    assert Path(v).exists()


def test_uncatalog_removes_db_keeps_file(service: VideoOrganizerService, tmp_path: Path):
    v = _touch(tmp_path / "y.mp4")
    service.db.upsert_video({"path": v, "filename": "y.mp4", "status": "pending", "tags": []})
    service.uncatalog_videos([v])
    assert service.db.get_video_by_path(v) is None
    assert Path(v).exists()


def test_library_id_stable(service: VideoOrganizerService, tmp_path: Path):
    v = _touch(tmp_path / "z.mp4")
    service.db.upsert_video({"path": v, "filename": "z.mp4", "status": "pending", "tags": []})
    row1 = service.db.get_video_by_path(v)
    lid = row1.get("library_id") or row1.get("id")
    assert lid is not None
    service.db.upsert_video({**row1, "summary": "x"})
    row2 = service.db.get_video_by_path(v)
    assert (row2.get("library_id") or row2.get("id")) == lid


def test_append_work_scope_keeps_exclusions(service: VideoOrganizerService, tmp_path: Path):
    """累加路径不得清空「移出工作范围」排除集。"""
    root = tmp_path / "clips"
    v = _touch(root / "x.mp4")
    extra = tmp_path / "more"
    extra.mkdir()
    service.set_work_scope_paths([str(root.resolve())], scan=True)
    service.remove_videos_from_work_scope([v])
    assert not any(x.get("path") == v for x in service.get_videos_in_work_scope())

    service.append_work_scope([str(extra.resolve())], scan=False)
    # 仍应排除
    assert not any(x.get("path") == v for x in service.get_videos_in_work_scope())
    assert normalize_work_path(v) in service._work_scope_exclusions


def test_exclusions_persist_when_remember_enabled(service: VideoOrganizerService, tmp_path: Path):
    from core.video_organizer_service import normalize_work_path

    root = tmp_path / "clips"
    v = _touch(root / "z.mp4")
    service.settings.setdefault("ui_preferences", {})["remember_work_scope"] = True
    service.set_work_scope_paths([str(root.resolve())], scan=True)
    service.remove_videos_from_work_scope([v])
    service.persist_work_scope_if_enabled()

    excl = service.settings["ui_preferences"].get("last_work_scope_exclusions") or []
    assert normalize_work_path(v) in {normalize_work_path(p) for p in excl}

    # 模拟重启：新实例恢复
    service2 = VideoOrganizerService(
        settings=service.settings,
        on_log=lambda _m: None,
        db_path=service.db.db_path,
        results_json=str(tmp_path / "r2.json"),
        results_csv=str(tmp_path / "r2.csv"),
    )
    service2.settings.setdefault("ui_preferences", {})["remember_work_scope"] = True
    assert service2.restore_work_scope_if_enabled() is True
    assert not any(
        normalize_work_path(x.get("path", "")) == normalize_work_path(v)
        for x in service2.get_videos_in_work_scope()
    )


def test_force_reanalyze_bypasses_api_cache(service: VideoOrganizerService, tmp_path: Path, monkeypatch):
    """强制重新分析不得读 API 缓存（use_cache=False）。"""
    v = _touch(tmp_path / "c.mp4")
    service.db.upsert_video(
        {
            "path": v,
            "filename": "c.mp4",
            "status": "analyzed",
            "tags": ["旧"],
            "category": "旧类",
            "summary": "旧摘要",
        }
    )
    calls = {"use_cache": []}

    def fake_analyze(frames, cache_key=None, use_cache=True):
        calls["use_cache"].append(use_cache)
        return {
            "tags": ["新词"],
            "category": "新类",
            "summary": "新摘要",
            "tag_groups": {},
        }

    monkeypatch.setattr(service.ai, "analyze_video", fake_analyze)
    monkeypatch.setattr(
        service.processor,
        "extract_frames",
        lambda *a, **k: {"frames": ["x"], "thumbnail": None, "phash": ""},
    )
    monkeypatch.setattr(service.processor, "get_file_hash", lambda p: "hash")
    monkeypatch.setattr(service, "sync_metadata_to_xmp", lambda *_a, **_k: None)
    monkeypatch.setattr(service, "_save_l3_backup", lambda: None)
    monkeypatch.setattr(service.file_manager, "save_results_to_csv", lambda *_a, **_k: None)
    monkeypatch.setattr(service.tag_processor, "process", lambda tags, cat: (list(tags or []), cat))
    monkeypatch.setattr(service.ai, "apply_tag_normalization", lambda data, **kw: data)

    r2 = service.run_analysis([v], force_reanalyze=True)
    assert r2["attempted"] == 1
    assert r2["succeeded"] == 1
    assert calls["use_cache"] == [False]
    row = service.db.get_video_by_path(v)
    assert row["summary"] == "新摘要"
    assert "新词" in (row.get("tags") or [])


def test_apply_pending_suggestion_dataclass_zero_id(service: VideoOrganizerService):
    """pending_id=0 等假值不得因 or .get 崩溃。"""
    from core.tag_ai_assist import PendingTagSuggestion

    # 写入 id 可控的待审：用 add 后改不了 id，改为 mock resolve
    called = {}

    def fake_resolve(pid, action, **kw):
        called["pid"] = pid
        called["action"] = action
        return True

    service.resolve_pending_tag = fake_resolve
    sug = PendingTagSuggestion(
        pending_id=0,
        raw_text="x",
        group_id="mood",
        action="discard",
    )
    assert service.apply_pending_suggestion(sug, confirm=True) is True
    assert called["pid"] == 0
    assert called["action"] == "discard"
