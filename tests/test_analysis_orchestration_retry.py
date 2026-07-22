# -*- coding: utf-8 -*-
"""分析任务编排：调用层/单条层/批次补跑/取消（ticket 02–03 + review 加固）。"""
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.analysis_job_policy import ApiCallFailure
from core.video_organizer_service import DEFAULT_SETTINGS, VideoOrganizerService


@pytest.fixture
def service(tmp_path: Path) -> VideoOrganizerService:
    settings = {
        **DEFAULT_SETTINGS,
        "ui_preferences": dict(DEFAULT_SETTINGS.get("ui_preferences", {})),
        "processing": {
            **DEFAULT_SETTINGS["processing"],
            "analysis_retry": {
                "call_extra_attempts": 2,
                "item_max_attempts": 2,
                "batch_rerun_enabled": True,
                "batch_rerun_max_rounds": 1,
            },
            "max_workers": 2,
        },
    }
    svc = VideoOrganizerService(
        settings=settings,
        on_log=lambda _m: None,
        db_path=str(tmp_path / "t.db"),
        results_json=str(tmp_path / "r.json"),
        results_csv=str(tmp_path / "r.csv"),
    )
    svc._retry_sleeper = lambda _s: None
    return svc


def _touch(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    return str(path.resolve())


def test_item_layer_retries_when_analyze_returns_none_retriable(
    service: VideoOrganizerService, tmp_path: Path, monkeypatch
):
    v = _touch(tmp_path / "a.mp4")
    service.db.upsert_video({"path": v, "filename": "a.mp4", "status": "pending", "tags": []})
    calls = {"n": 0}

    def flaky2(frames, cache_key=None, use_cache=True):
        calls["n"] += 1
        if calls["n"] == 1:
            service.ai._last_api_failure = ApiCallFailure(
                message="empty body", retriable=True
            )
            return None
        service.ai._last_api_failure = None
        return {
            "tags": ["治愈"],
            "category": "Broll",
            "summary": "ok",
            "tag_groups": {},
        }

    monkeypatch.setattr(service.ai, "analyze_video", flaky2)
    monkeypatch.setattr(
        service.processor,
        "extract_frames",
        lambda *a, **k: {"frames": ["x"], "thumbnail": None, "phash": ""},
    )
    monkeypatch.setattr(service.processor, "get_file_hash", lambda p: "h")
    monkeypatch.setattr(service, "sync_metadata_to_xmp", lambda *_a, **_k: 1)
    monkeypatch.setattr(service, "_save_l3_backup", lambda: None)
    monkeypatch.setattr(service.file_manager, "save_results_to_csv", lambda *_a, **_k: None)
    monkeypatch.setattr(service.tag_processor, "process", lambda tags, cat: (list(tags or []), cat))
    monkeypatch.setattr(service.ai, "apply_tag_normalization", lambda data, **kw: data)

    r = service.run_analysis([v])
    assert r["succeeded"] == 1
    assert calls["n"] == 2


def test_auth_error_does_not_item_or_batch_retry(
    service: VideoOrganizerService, tmp_path: Path, monkeypatch
):
    """鉴权失败：B 不重试，默认 C 也不补跑。"""
    v = _touch(tmp_path / "auth.mp4")
    service.db.upsert_video({"path": v, "filename": "auth.mp4", "status": "pending", "tags": []})
    calls = {"n": 0}

    def auth_fail(frames, cache_key=None, use_cache=True):
        calls["n"] += 1
        fail = ApiCallFailure(message="401 Unauthorized", retriable=False)
        service.ai._last_api_failure = fail
        if getattr(service.ai, "_tls", None) is not None:
            service.ai._tls.last_api_failure = fail
        return None

    monkeypatch.setattr(service.ai, "analyze_video", auth_fail)
    monkeypatch.setattr(
        service.processor,
        "extract_frames",
        lambda *a, **k: {"frames": ["x"], "thumbnail": None, "phash": ""},
    )
    monkeypatch.setattr(service.processor, "get_file_hash", lambda p: "h")
    monkeypatch.setattr(service, "_save_l3_backup", lambda: None)
    monkeypatch.setattr(service.file_manager, "save_results_to_csv", lambda *_a, **_k: None)

    r = service.run_analysis([v])
    assert r["failed"] == 1
    assert r["batch_rerun"] is False
    assert calls["n"] == 1
    assert "401" in (r.get("failure_reasons") or [""])[0] or "401" in r["message"]


def test_call_layer_retries_empty_body(service: VideoOrganizerService, tmp_path: Path, monkeypatch):
    """直接测 _get_api_response：empty body 可 A 重试。"""
    n = {"c": 0}

    class FakeChoices:
        def __init__(self, content):
            self.message = SimpleNamespace(content=content)

    class FakeResp:
        def __init__(self, content):
            self.choices = [FakeChoices(content)]

    def create(**kwargs):
        n["c"] += 1
        if n["c"] < 3:
            return FakeResp("")
        return FakeResp('{"tags": [], "summary": "x", "category": "Broll"}')

    service.ai.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    service.ai._retry_sleeper = lambda _s: None
    service.ai._job_retry_config = None
    from core.analysis_job_policy import RetryConfig

    service.ai._job_retry_config = RetryConfig(call_extra_attempts=2)

    data = service.ai._get_api_response("m", "sys", [{"type": "text", "text": "u"}])
    assert data is not None
    assert n["c"] == 3


def test_batch_rerun_recovers_failure(service: VideoOrganizerService, tmp_path: Path, monkeypatch):
    v = _touch(tmp_path / "b.mp4")
    service.db.upsert_video({"path": v, "filename": "b.mp4", "status": "pending", "tags": []})
    state = {"round": 0}

    def fake_process(path, force_reanalyze=False, progress_reducer=None, retry_cfg=None):
        state["round"] += 1
        if state["round"] == 1:
            return None, "empty body"  # 瞬时失败，应进 C
        return {
            "path": path,
            "filename": "b.mp4",
            "status": "analyzed",
            "tags": ["ok"],
            "category": "Broll",
            "summary": "s",
            "tag_groups": {},
        }, None

    monkeypatch.setattr(service, "_process_single_video", fake_process)
    monkeypatch.setattr(service, "sync_metadata_to_xmp", lambda *_a, **_k: 1)
    monkeypatch.setattr(service, "_save_l3_backup", lambda: None)
    monkeypatch.setattr(service.file_manager, "save_results_to_csv", lambda *_a, **_k: None)

    r = service.run_analysis([v])
    assert r["batch_rerun"] is True
    assert r["succeeded"] == 1
    assert r["failed"] == 0


def test_batch_rerun_disabled(service: VideoOrganizerService, tmp_path: Path, monkeypatch):
    service.settings["processing"]["analysis_retry"]["batch_rerun_enabled"] = False
    v = _touch(tmp_path / "c.mp4")
    service.db.upsert_video({"path": v, "filename": "c.mp4", "status": "pending", "tags": []})
    n = {"c": 0}

    def always_fail(path, force_reanalyze=False, progress_reducer=None, retry_cfg=None):
        n["c"] += 1
        return None, "fail"

    monkeypatch.setattr(service, "_process_single_video", always_fail)
    monkeypatch.setattr(service, "_save_l3_backup", lambda: None)
    monkeypatch.setattr(service.file_manager, "save_results_to_csv", lambda *_a, **_k: None)

    r = service.run_analysis([v])
    assert r["batch_rerun"] is False
    assert r["failed"] == 1
    assert n["c"] == 1


def test_cancel_skips_batch_rerun(service: VideoOrganizerService, tmp_path: Path, monkeypatch):
    paths = [_touch(tmp_path / f"{i}.mp4") for i in range(3)]
    for p in paths:
        service.db.upsert_video(
            {"path": p, "filename": Path(p).name, "status": "pending", "tags": []}
        )
    seen = []

    def fail_and_cancel(path, force_reanalyze=False, progress_reducer=None, retry_cfg=None):
        seen.append(path)
        service.request_cancel_analysis()
        return None, "用户取消"

    monkeypatch.setattr(service, "_process_single_video", fail_and_cancel)
    monkeypatch.setattr(service, "_save_l3_backup", lambda: None)
    monkeypatch.setattr(service.file_manager, "save_results_to_csv", lambda *_a, **_k: None)

    r = service.run_analysis(paths)
    assert r["batch_rerun"] is False
    assert r.get("cancelled") or "取消" in r["message"]
    assert len(seen) <= 3


def test_xmp_fail_still_success(service: VideoOrganizerService, tmp_path: Path, monkeypatch):
    v = _touch(tmp_path / "x.mp4")
    service.db.upsert_video({"path": v, "filename": "x.mp4", "status": "pending", "tags": []})

    def ok(path, force_reanalyze=False, progress_reducer=None, retry_cfg=None):
        return {
            "path": path,
            "filename": "x.mp4",
            "status": "analyzed",
            "tags": ["t"],
            "category": "Broll",
            "summary": "s",
            "tag_groups": {},
        }, None

    monkeypatch.setattr(service, "_process_single_video", ok)

    def boom(*_a, **_k):
        raise RuntimeError("xmp disk full")

    monkeypatch.setattr(service, "sync_metadata_to_xmp", boom)
    monkeypatch.setattr(service, "_save_l3_backup", lambda: None)
    monkeypatch.setattr(service.file_manager, "save_results_to_csv", lambda *_a, **_k: None)

    r = service.run_analysis([v])
    assert r["succeeded"] == 1
    assert r.get("xmp_failed", 0) >= 1
    assert service.db.get_video_by_path(v)["status"] == "analyzed"