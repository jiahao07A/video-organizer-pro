"""400恢复集成测试：使用真实网关/调度/SQLite，替换外部传输和视频解码。"""
from tests.support.recovery import HttpError, response, Client, service as service, video, configure, clients, fallback
import json
import sqlite3
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from core.analysis_recovery import (
    RecoveryContext, RecoveryDeferred, RecoveryScheduler,
    RecoveryStore, effective_policy, validate_policy,
)
from core.ai_gateway import AiRequestGateway
from core.video_catalog import VideoCatalog, VideoQuery
from core.video_organizer_service import DatabaseManager
from core.startup_upgrade import StartupUpgradeCoordinator, UpgradeStatus


def test_primary_recovers_and_records_once(service, tmp_path, monkeypatch):
    path = video(service, tmp_path)
    cs = clients(service, monkeypatch, main=[HttpError(), response()])
    result = service.run_analysis([path])
    assert (result["succeeded"], result["failed"], result["batch_rerun"]) == (1, 0, False)
    detail = service.get_recovery_detail(path)
    assert detail["state"] == "recovered"
    assert detail["detail"]["used"] == 1
    assert len(detail["rounds"]) == 1
    assert len(detail["attempts"]) == len(cs["main"].calls) == 2
    assert detail["attempts"][-1]["detail"]["status_code"] == 400
    assert detail["attempts"][-1]["detail"]["error_code"] == "bad_input"
    assert result["metrics"]["api_attempts"] == 2
    assert not result["failure_reasons"]


def test_fallback_initialization_failure_skips_without_request_budget(service, tmp_path, monkeypatch):
    path = video(service, tmp_path)
    configure(service, primary_extra_attempts=0, request_budget=1,
              fallbacks=[fallback(), fallback("last")])
    cs = {"main": Client([HttpError()]), "last": Client([response()])}
    initialized = []

    def get_client(route):
        name = route["provider_id"]
        initialized.append(name)
        if name == "backup":
            raise ValueError("invalid connection secret-backup")
        return cs[name]

    monkeypatch.setattr(service.ai.gateway, "client_for_route", get_client)
    result = service.run_analysis([path])
    assert result["succeeded"] == 1
    assert initialized == ["main", "backup", "last"]
    detail = service.get_recovery_detail(path)
    assert detail["detail"]["used"] == 1
    assert detail["detail"]["successful_route"]["provider_id"] == "last"
    assert "secret-backup" not in json.dumps(detail)


@pytest.mark.parametrize("timing", ["after_batch", "immediate"])
def test_ordered_fallback_auth_skip_and_success(service, tmp_path, monkeypatch, timing):
    path = video(service, tmp_path)
    configure(service, primary_extra_attempts=1, fallback_timing=timing,
              fallbacks=[fallback(), fallback("last")], fallback_attempts=3)
    cs = clients(service, monkeypatch, main=[HttpError()], backup=[HttpError(401)], last=[response()])
    result = service.run_analysis([path])
    assert result["succeeded"] == 1
    assert [len(cs[k].calls) for k in ("main", "backup", "last")] == [2, 1, 1]
    detail = service.get_recovery_detail(path)
    assert detail["state"] == "recovered"
    assert detail["detail"]["successful_route"]["provider_id"] == "last"
    assert service.settings["task_model_routing"]["video_classification"]["provider_id"] == "main"


@pytest.mark.parametrize("budget", [1, 2, 5])
def test_budget_includes_all_routes_without_legacy_retry(service, tmp_path, monkeypatch, budget):
    path = video(service, tmp_path)
    configure(service, request_budget=budget, primary_extra_attempts=10, fallbacks=[fallback()])
    cs = clients(service, monkeypatch, main=[HttpError(message="400 timeout")], backup=[response()])
    result = service.run_analysis([path])
    assert result["failed"] == 1 and not result["batch_rerun"]
    assert len(cs["main"].calls) == 1 + budget
    assert not cs["backup"].calls
    assert service.get_recovery_detail(path)["state"] == "exhausted"


@pytest.mark.parametrize("policy,state", [({"enabled": False}, "disabled"),
    ({"exclusions": [{"error_code": "bad_input"}]}, "excluded"),
    ({"exclusions": [{"keyword": "unsafe"}]}, "excluded")])
def test_disabled_and_excluded_only_mark(service, tmp_path, monkeypatch, policy, state):
    path = video(service, tmp_path)
    configure(service, **policy)
    cs = clients(service, monkeypatch, main=[HttpError(message="image_url unsafe")])
    service.run_analysis([path])
    assert len(cs["main"].calls) == 1
    assert service.get_recovery_detail(path)["state"] == state


def test_response_format_compatibility_is_counted(service, tmp_path, monkeypatch):
    path = video(service, tmp_path)
    configure(service, request_budget=1)
    cs = clients(service, monkeypatch, main=[HttpError(message="unsupported response_format json_object"), response()])
    service.run_analysis([path])
    assert len(cs["main"].calls) == 2
    assert "response_format" not in cs["main"].calls[1]
    assert service.get_recovery_detail(path)["detail"]["used"] == 1


def test_failed_reanalysis_preserves_success_and_manual_bypasses_cache(service, tmp_path, monkeypatch):
    path = video(service, tmp_path, status="renamed")
    configure(service, primary_extra_attempts=0)
    cs = clients(service, monkeypatch, main=[HttpError()])
    service.run_analysis([path], force_reanalyze=True)
    old = service.db.get_video_by_path(path)
    assert (old["status"], old["category"], old["summary"], old["tags"]) == ("renamed", "旧分类", "旧摘要", ["旧标签"])
    cs["main"].outputs = [response()]
    result = service.run_analysis([path], recovery_mode="primary")
    assert result["succeeded"] == 1
    assert len(cs["main"].calls) == 2
    assert len(service.get_recovery_detail(path)["rounds"]) == 2
    assert service.db.get_video_by_path(path)["summary"] == "新分析结果"


def test_selected_mode_sends_only_selected_provider(service, tmp_path, monkeypatch):
    path = video(service, tmp_path)
    cs = clients(service, monkeypatch, main=[HttpError()], backup=[response()])
    service.run_analysis([path], recovery_mode="selected", selected_fallback=fallback())
    assert not cs["main"].calls and len(cs["backup"].calls) == 1
    assert service.get_recovery_detail(path)["state"] == "recovered"


def test_cancellation_during_wait_stops_all_future_calls(service, tmp_path, monkeypatch):
    path = video(service, tmp_path)
    configure(service, delay_seconds=30, fallbacks=[fallback()])
    cs = clients(service, monkeypatch, main=[HttpError()], backup=[response()])
    def on_snapshot(snap):
        detail = service.get_recovery_detail(path)
        if detail and detail["state"] == "pending":
            service.request_cancel_analysis()
    service.on_analysis_snapshot = on_snapshot
    result = service.run_analysis([path])
    assert result["cancelled"]
    assert len(cs["main"].calls) == 1 and not cs["backup"].calls
    assert service.get_recovery_detail(path)["state"] == "cancelled"


def test_inheritance_empty_list_false_and_validation():
    settings = {"processing": {"http400_recovery": {"global": {"fallbacks": [fallback()], "enabled": True},
                 "tasks": {"video_classification": {"primary_extra_attempts": 4}}}}}
    policy, sources = effective_policy(settings, video={"fallbacks": [], "enabled": False}, temporary={"primary_extra_attempts": 0})
    assert policy["fallbacks"] == [] and not policy["enabled"] and policy["primary_extra_attempts"] == 0
    assert sources["enabled"] == "视频" and sources["primary_extra_attempts"] == "本次"
    for bad in ({"request_budget": 0}, {"request_budget": 1.5}, {"jitter": float("nan")}, {"fallbacks": [{"api_key": "secret"}]}, {"exclusions": [{}]}):
        with pytest.raises(ValueError):
            validate_policy(bad)


def test_query_pagination_history_path_move_and_restart(service, tmp_path, monkeypatch):
    paths = [video(service, tmp_path, f"{i}.mp4") for i in range(4)]
    configure(service, primary_extra_attempts=0)
    clients(service, monkeypatch, main=[HttpError()])
    service.run_analysis(paths)
    query = VideoQuery(recovery_filter="unresolved")
    page = VideoCatalog(service.db).list_page(query, limit=1)
    assert page.total_count == 4 and len(page.rows) == 1
    assert len(VideoCatalog(service.db).resolve_visible_paths(query)) == 4
    assert len(service.recovery_target_paths(paths)) == 4
    service.save_recovery_override([paths[0]], {"enabled": False})
    moved = str(tmp_path / "moved.mp4")
    service.db.update_video_path(paths[0], moved)
    assert RecoveryStore(service.db).override(moved) == {"enabled": False}
    detail = service.get_recovery_detail(moved)
    service.db.execute_non_query("UPDATE video_recovery SET state='waiting_fallback' WHERE video_id=?", (detail["video_id"],))
    RecoveryStore(service.db).interrupt()
    assert service.get_recovery_detail(moved)["state"] == "interrupted"
    service.db.delete_video(moved)
    assert not service.db.execute_query("SELECT * FROM video_recovery WHERE video_id=?", (detail["video_id"],))


def test_thread_local_failure_does_not_leak_success():
    gateway = AiRequestGateway({})
    barrier = threading.Barrier(2)
    def run(fail):
        client = Client([HttpError()] if fail else [response()])
        result = gateway.request_chat(model="m", system_prompt="s", content_parts=[], client=client)
        barrier.wait(timeout=5)
        return result, gateway.get_last_failure()
    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = pool.submit(run, True), pool.submit(run, False)
        assert a.result()[1].status_code == 400
        assert b.result()[1] is None


def test_schema_upgrade_backs_up_and_preserves_data(tmp_path):
    db = DatabaseManager(str(tmp_path / "v.db"))
    db.upsert_video({"path": "v.mp4", "filename": "v.mp4", "summary": "保留"})
    with sqlite3.connect(db.db_path) as conn:
        conn.execute("PRAGMA user_version=1")
    coordinator = StartupUpgradeCoordinator(database_path=db.db_path, json_path=str(tmp_path / "r.json"),
        backup_dir=str(tmp_path / "backups"), settings_path=str(tmp_path / "s.json"), tag_config_path=str(tmp_path / "t.json"))
    assert coordinator.inspect().report["schema_upgrade"]
    assert coordinator.execute(confirmed=False).status == UpgradeStatus.CANCELLED
    result = coordinator.execute(confirmed=True)
    assert result.status == UpgradeStatus.COMPLETED and result.backup_path
    assert db.get_video_by_path("v.mp4")["summary"] == "保留"
    with sqlite3.connect(db.db_path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 5


def test_redaction_and_no_plaintext_credentials_in_history(service, tmp_path, monkeypatch):
    path = video(service, tmp_path)
    configure(service, primary_extra_attempts=0)
    clients(service, monkeypatch, main=[HttpError(message="key secret-main https://main.invalid/v1?key=secret-main data:image/jpeg;base64,AAAA")])
    service.run_analysis([path])
    text = json.dumps(service.get_recovery_detail(path))
    assert "secret-main" not in text and "base64,AAAA" not in text


def test_duplicate_analysis_is_rejected(service):
    service._analysis_run_lock.acquire()
    try:
        with pytest.raises(RuntimeError, match="已有分析任务"):
            service.run_analysis([])
    finally:
        service._analysis_run_lock.release()


def test_audio_400_uses_task_policy_and_does_not_repeat_video_or_side_effects(service, tmp_path, monkeypatch):
    path = video(service, tmp_path)
    service.settings["processing"]["enable_audio_transcription"] = True
    service.settings["processing"]["http400_recovery"]["tasks"] = {"content_description": {
        "primary_extra_attempts": 0, "fallbacks": [{"provider_id": "backup", "model": "transcription-backup"}],
    }}
    service.settings["task_model_routing"]["content_description"] = {"provider_id": "main", "model": "unused-description"}
    def fake_run(command, **_):
        if "-y" in command:
            Path(command[-1]).write_bytes(b"synthetic audio")
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr("core.video_organizer_service.subprocess.run", fake_run)
    cs = clients(service, monkeypatch, main=[response(), HttpError()], backup=["恢复的转录"])
    writes = []
    original = service.ai.apply_tag_normalization
    def normalize(data, **kwargs):
        if kwargs.get("persist_pending"):
            writes.append(True)
        return original(data, **kwargs)
    monkeypatch.setattr(service.ai, "apply_tag_normalization", normalize)
    outcome = service.run_analysis([path])
    assert outcome["succeeded"] == 1 and outcome["failed"] == 0
    assert len(cs["main"].calls) == 2 and len(cs["backup"].calls) == 1
    assert cs["backup"].calls[0]["model"] == "transcription-backup"
    assert service.db.get_video_by_path(path)["transcription"] == "恢复的转录"
    assert writes == []  # 待审词由最终数据库成功事务统一写入。
    detail = service.get_recovery_detail(path)
    assert detail["state"] == "recovered"
    assert detail["detail"]["last_error"]["task_key"] == "content_description"
    assert set(detail["detail"]["successful_routes"]) == {"video_classification", "content_description"}


def test_missing_file_in_manual_round_is_terminal(service, tmp_path):
    path = video(service, tmp_path)
    Path(path).unlink()
    outcome = service.run_analysis([path], recovery_mode="primary")
    assert outcome["failed"] == 1
    assert service.get_recovery_detail(path)["state"] == "exhausted"


def test_cache_isolated_by_actual_fallback_provider(service, tmp_path, monkeypatch):
    path = video(service, tmp_path)
    configure(service, primary_extra_attempts=0, fallbacks=[fallback()])
    cs = clients(service, monkeypatch, main=[HttpError()], backup=[response()])
    assert service.run_analysis([path])["succeeded"] == 1
    cs["main"].outputs = [response()]
    # 新的普通强制轮次必须真正访问主供应商，不能命中兜底结果。
    assert service.run_analysis([path], force_reanalyze=True)["succeeded"] == 1
    assert len(cs["main"].calls) == 2
    assert len(service.db.execute_query("SELECT cache_key FROM api_cache")) == 2


def test_backfill_only_explicit_http_400(tmp_path):
    db = DatabaseManager(str(tmp_path / "old.db"))
    for i, message in enumerate(("HTTP 400 bad request", "镜头400文件.mp4无法打开", "Error code: 400 image_url unsafe")):
        db.upsert_video({"path": f"{i}.mp4", "status": "failed", "raw_metadata": {"last_error": message}})
    with sqlite3.connect(db.db_path) as conn:
        conn.execute("PRAGMA user_version=1")
    upgraded = DatabaseManager(db.db_path)
    assert upgraded.execute_query("SELECT COUNT(*) n FROM video_recovery")[0]["n"] == 3
    assert upgraded.execute_query("SELECT COUNT(*) n FROM analysis_failure_index WHERE ever400=1")[0]["n"] == 2
    assert len(list(tmp_path.glob("*.pre-recovery-*.sqlite"))) == 1


def test_delayed_retry_leaves_worker_available():
    clock = [0.0]
    contexts = {key: SimpleNamespace(active=False) for key in ("a", "b")}
    scheduler = RecoveryScheduler(contexts, threading.Event(), clock=lambda: clock[0])
    calls = []
    def process(path):
        calls.append(path)
        if calls == ["a"]:
            raise RecoveryDeferred(contexts[path], delay=10)
        if path == "b":
            clock[0] = 10.0
        return {"ok": True}, None
    outcomes = list(scheduler.run(["a", "b"], process, max_workers=1))
    assert calls == ["a", "b", "a"]
    assert [item[0] for item in outcomes] == ["b", "a"]


def test_policy_and_routes_are_frozen_during_batch(service, tmp_path, monkeypatch):
    path = video(service, tmp_path)
    configure(service, request_budget=2, primary_extra_attempts=10)
    def change_settings():
        configure(service, request_budget=50, primary_extra_attempts=0)
        service.settings["task_model_routing"]["video_classification"]["model"] = "changed-live"
        return HttpError()
    cs = clients(service, monkeypatch, main=[change_settings])
    outcome = service.run_analysis([path])
    assert outcome["failed"] == 1
    assert len(cs["main"].calls) == 3
    assert {c["model"] for c in cs["main"].calls} == {"vision-main"}


def test_budget_shared_with_later_audio_request(service, tmp_path, monkeypatch):
    path = video(service, tmp_path)
    configure(service, request_budget=2, fallbacks=[fallback()])
    service.settings["processing"]["enable_audio_transcription"] = True
    service.settings["task_model_routing"]["content_description"] = {"provider_id": "main", "model": "unused"}
    def fake_run(command, **_):
        if "-y" in command:
            Path(command[-1]).write_bytes(b"audio")
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr("core.video_organizer_service.subprocess.run", fake_run)
    cs = clients(service, monkeypatch, main=[HttpError(), response(), HttpError()], backup=[response()])
    outcome = service.run_analysis([path])
    assert outcome["failed"] == 1 and not cs["backup"].calls
    assert len(cs["main"].calls) == 3
    assert service.get_recovery_detail(path)["detail"]["used"] == 2


def test_upgrade_failure_rolls_back_schema_and_keeps_backup(tmp_path, monkeypatch):
    db = DatabaseManager(str(tmp_path / "v.db"))
    db.upsert_video({"path": "v.mp4", "summary": "旧数据"})
    with sqlite3.connect(db.db_path) as conn:
        conn.execute("PRAGMA user_version=1")
    def fail_install(conn):
        conn.execute("CREATE TABLE incomplete_upgrade(value TEXT)")
        raise RuntimeError("injected schema failure")
    monkeypatch.setattr("core.analysis_recovery.install_schema", fail_install)
    coordinator = StartupUpgradeCoordinator(database_path=db.db_path, json_path=str(tmp_path / "r.json"), backup_dir=str(tmp_path / "backups"), settings_path=str(tmp_path / "s.json"), tag_config_path=str(tmp_path / "t.json"))
    outcome = coordinator.execute(confirmed=True)
    assert outcome.status == UpgradeStatus.FAILED and outcome.backup_path
    with sqlite3.connect(db.db_path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
        assert not conn.execute("SELECT name FROM sqlite_master WHERE name='incomplete_upgrade'").fetchone()
        assert conn.execute("SELECT summary FROM videos").fetchone()[0] == "旧数据"


def test_success_metadata_recovery_and_tag_effects_are_atomic(service, tmp_path):
    path = video(service, tmp_path, status="analyzed")
    service.db.execute_non_query("INSERT INTO tags_library(tag_name,dimension,usage_count) VALUES (?,?,?)", ("计数词", "mood", 0))
    store = RecoveryStore(service.db)
    policy, _ = effective_policy(service.settings)
    context = RecoveryContext(path, service.settings, policy, store)
    context.activate()
    row = {**service.db.get_video_by_path(path), "summary": "新数据", "tags": ["计数词"],
           "_analysis_side_effects": True, "_recovery_round": context.round_id, "_recovery_detail": context.detail(),
           "raw_metadata": {"_pending_tags": [{"raw_text": "候选词", "group_id": "mood"}]}}
    service.db.execute_non_query("CREATE TRIGGER fail_recovery BEFORE UPDATE ON video_recovery BEGIN SELECT RAISE(ABORT, 'injected'); END")
    with pytest.raises(sqlite3.IntegrityError):
        service.db.upsert_video(row)
    assert service.db.get_video_by_path(path)["summary"] == "旧摘要"
    assert service.db.execute_query("SELECT usage_count FROM tags_library WHERE tag_name='计数词'")[0]["usage_count"] == 0
    assert not service.db.execute_query("SELECT * FROM pending_tags WHERE raw_text='候选词'")
    service.db.execute_non_query("DROP TRIGGER fail_recovery")
    service.db.upsert_video(row)
    assert store.detail(path)["state"] == "recovered"
    assert service.db.execute_query("SELECT usage_count FROM tags_library WHERE tag_name='计数词'")[0]["usage_count"] == 1
    assert len(service.db.execute_query("SELECT * FROM pending_tags WHERE raw_text='候选词'")) == 1


def test_sequential_visual_and_audio_deferred_fallbacks_complete(service, tmp_path, monkeypatch):
    path = video(service, tmp_path)
    configure(service, primary_extra_attempts=0, fallbacks=[fallback()])
    service.settings["processing"]["enable_audio_transcription"] = True
    service.settings["task_model_routing"]["content_description"] = {"provider_id": "main", "model": "unused"}
    service.settings["processing"]["http400_recovery"]["tasks"] = {"content_description": {"fallbacks": [{"provider_id": "backup", "model": "whisper-backup"}]}}
    def fake_run(command, **_):
        if "-y" in command:
            Path(command[-1]).write_bytes(b"audio")
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr("core.video_organizer_service.subprocess.run", fake_run)
    cs = clients(service, monkeypatch, main=[HttpError()], backup=[response(), "最终音频转录"])
    result = service.run_analysis([path])
    assert (result["succeeded"], result["failed"]) == (1, 0)
    assert len(cs["main"].calls) == len(cs["backup"].calls) == 2
    assert service.get_recovery_detail(path)["state"] == "recovered"
