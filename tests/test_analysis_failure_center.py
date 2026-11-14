"""分析失败中心的分类、查询、预览和诊断缓存。"""
from tests.support.failure_center import service as service
import json
from types import SimpleNamespace
import sqlite3
import pytest

from core.analysis_failures import CATEGORIES, FailureQuery, FailureStore, classify, install_schema
from core.analysis_recovery import RecoveryContext, RecoveryStore
from tests.support.recovery import video, clients, HttpError


def test_classify_structured_errors_and_redacts_path():
    result = classify({"status_code": 401, "error_code": "invalid_api_key", "message": r"C:\private\clip.mp4 unauthorized", "task_key": "video_classification"})
    assert result["category"] == "auth"
    assert CATEGORIES[result["category"]] == "鉴权/权限"
    assert "<PATH>" in result["evidence"]


def test_failure_query_returns_counts_and_filters(service, tmp_path):
    path = video(service, tmp_path)
    service.db.execute_non_query("INSERT INTO video_recovery(video_id,state,round_id,updated_at,detail) SELECT id,'failed','r1','2026-11-08',? FROM videos WHERE path=?", (json.dumps({"last_error": {"status_code": 429, "error_code": "rate_limit", "message": "rate limit", "task_key": "video_classification"}}), path))
    service.db.execute_non_query("INSERT INTO video_recovery_rounds(id,video_id,started_at,ended_at,mode,policy) SELECT 'r1',id,'2026-11-08','2026-11-08','test','{}' FROM videos WHERE path=?", (path,))
    with service.db.lock, service.db._managed_connection() as conn:
        install_schema(conn)
        FailureStore(service.db)
        from core.analysis_failures import refresh_index
        row = conn.execute("SELECT id FROM videos WHERE path=?", (path,)).fetchone()
        refresh_index(conn, row[0])
    result = service.query_analysis_failures(FailureQuery(category="rate_limit"))
    assert result["total"] == 1
    assert result["rows"][0]["path"] == path
    other = video(service, tmp_path, name="auth.mp4")
    record_service_failure(service, other, message="service unavailable")
    for query, expected in [(FailureQuery(category="rate_limit"), path), (FailureQuery(category="service"), other)]:
        result = service.query_analysis_failures(query)
        assert result["total"] == 1
        assert result["counts"] == {query.category: 1}
        assert [row["path"] for row in result["rows"]] == [expected]
        targets = FailureStore(service.db).query(query, paths_only=True)
        assert targets == [expected]
        assert service.preview_manual_retry(targets)["paths"] == targets


def test_diagnosis_redacts_actual_outbound_payload(service, monkeypatch):
    from core.analysis_failures import FailureStore
    raw = "timeout secret-main C:/private/client/clip.mp4 data:image/jpeg;base64,QUJDREVGR0g="
    group = {"fingerprint": "synthetic", "material_count": 1, "category": "network",
             "stage": "video_classification", "provider_id": "main", "model": "vision",
             "status_code": 504, "message": raw, "evidence": raw, "suggestion": "retry"}
    monkeypatch.setattr(FailureStore, "query", lambda *a, **kw: [group])
    monkeypatch.setattr(FailureStore, "diagnosis", lambda *a: None)
    monkeypatch.setattr(FailureStore, "save_diagnosis", lambda *a: None)
    sent = []
    def capture(model, prompt, content, **kwargs):
        sent.append(json.loads(content[0]["text"]))
        return {"cause": "timeout", "evidence": "504", "actions": ["retry"]}
    monkeypatch.setattr(service.ai, "_get_api_response", capture)
    assert service.diagnose_analysis_failures()["succeeded"] == 1
    assert len(sent) == 1
    encoded = json.dumps(sent[0])
    for secret in ("secret-main", "C:/private/client/clip.mp4", "data:image", "QUJDREVGR0g="):
        assert secret not in encoded
    for key in ("message", "evidence"):
        assert all(marker in sent[0][key] for marker in ("<REDACTED>", "<PATH>", "<IMAGE_REDACTED>"))
        assert "timeout" in sent[0][key]
    assert {k: sent[0][k] for k in ("category", "stage", "status_code", "provider_id", "model")} == {k: group[k] for k in ("category", "stage", "status_code", "provider_id", "model")}


def test_manual_preview_skips_missing_but_keeps_valid(service, tmp_path):
    path = video(service, tmp_path)
    result = service.preview_manual_retry([path, str(tmp_path / "missing.mp4")], manual_options={"concurrency": 1, "request_interval": 0})
    assert result["paths"] == [path]
    assert result["skipped"] and result["skipped"][0]["path"].endswith("missing.mp4")


def test_diagnosis_groups_and_reuses_cache(service, tmp_path, monkeypatch):
    paths = [video(service, tmp_path, name=f"clip{i}.mp4") for i in range(3)]
    from core.analysis_failures import refresh_index
    for i, path in enumerate(paths):
        error = {"status_code": 429 if i < 2 else 401, "error_code": "rate_limit" if i < 2 else "unauthorized",
                 "message": "rate limit" if i < 2 else "unauthorized", "task_key": "video_classification"}
        round_id = f"r{i}"
        service.db.execute_non_query("INSERT INTO video_recovery(video_id,state,round_id,updated_at,detail) SELECT id,'failed',?,'2026-11-08',? FROM videos WHERE path=?", (round_id, json.dumps({"last_error": error}), path))
        service.db.execute_non_query("INSERT INTO video_recovery_rounds(id,video_id,started_at,ended_at,mode,policy) SELECT ?,id,'2026-11-08','2026-11-08','test','{}' FROM videos WHERE path=?", (round_id, path))
        with service.db.lock, service.db._managed_connection() as conn:
            refresh_index(conn, conn.execute("SELECT id FROM videos WHERE path=?", (path,)).fetchone()[0])
    calls = []
    monkeypatch.setattr(service.ai, "_get_api_response", lambda *a, **kw: (calls.append(1) or {"cause": "限流", "confidence": 0.9, "evidence": "429", "actions": ["降低并发"], "retry_options": {}}))
    first = service.diagnose_analysis_failures(paths=paths)
    assert len(calls) == 2
    second = service.diagnose_analysis_failures(paths=paths)
    assert len(first["groups"]) == len(second["groups"]) == 2
    assert sorted(group["count"] for group in first["groups"]) == [1, 2]
    assert second["groups"] == first["groups"]
    assert len(calls) == 2


def test_version_two_upgrade_backfills_non400_without_inventing_attempts(service, tmp_path):
    from core.video_organizer_service import DatabaseManager
    path = video(service, tmp_path)
    service.db.upsert_video({"path": path, "status": "failed", "raw_metadata": {"last_error": "TimeoutError: timed out"}})
    with sqlite3.connect(service.db.db_path) as conn:
        conn.execute("PRAGMA user_version=2")
    db = DatabaseManager(service.db.db_path)
    row = FailureStore(db).query()["rows"][0]
    assert row["category"] == "network"
    assert row["attempt_count"] is None and row["provider_id"] == ""
    assert not row["ever400"]
    assert service.recovery_target_paths([path]) == []
    assert list(tmp_path.glob("*.pre-recovery-*.sqlite"))
    assert db.execute_query("PRAGMA user_version")[0]["user_version"] == 5


def test_failure_center_pages_and_preserves_empty_scope(service):
    from PySide6.QtWidgets import QApplication
    from gui.widgets.recovery_controls import AnalysisFailureCenterDialog
    app = QApplication.instance() or QApplication([])
    calls = []
    def query(q, **kwargs):
        calls.append((q, kwargs))
        return {"rows": [], "total": 250, "counts": {}}
    service.query_analysis_failures = query
    dialog = AnalysisFailureCenterDialog(service, paths=[])
    assert calls[-1][0].scope_paths == ()
    dialog.change_page(1)
    assert calls[-1][1] == {"offset": 100, "limit": 100}
    dialog.ever400.setChecked(True)
    assert calls[-1][0].ever400 and calls[-1][1]["offset"] == 0
    dialog.all_library.setChecked(True)
    assert calls[-1][0].scope_paths is None
    dialog.close()


def test_manual_preview_routes_each_stage_independently(service, tmp_path):
    path = video(service, tmp_path)
    service.settings["processing"]["enable_audio_transcription"] = True
    routes = {"video_classification": {"provider_id": "backup", "model": "vision"},
              "content_description": {"provider_id": "main", "model": "audio"}}
    preview = service.preview_manual_retry([path], recovery_mode="selected", manual_options={"stage_routes": routes})
    assert preview["paths"] == [path]
    assert preview["items"][0]["routes"]["video_classification"]["model"] == "vision"
    assert preview["items"][0]["routes"]["content_description"]["model"] == "audio"


def test_manual_preview_discloses_full_execution_route_chain(service, tmp_path):
    from core.analysis_recovery import RecoveryContext, RecoveryStore, effective_policy
    path = video(service, tmp_path)
    override = {"fallbacks": [{"provider_id": "backup", "model": "backup-vision", "attempts": 2}]}
    preview = service.preview_manual_retry([path], recovery_override=override)
    assert preview["paths"] == [path]
    item = preview["items"][0]
    context = RecoveryContext(path, service.settings, effective_policy(service.settings, temporary=override)[0], RecoveryStore(service.db), manual_options=preview["manual_options"])
    assert [(entry["provider_id"], entry["model"]) for entry in item["route_chains"]["video_classification"]] == [(route["provider_id"], route["model"]) for route in context.routes]
    assert item["route_chains"]["video_classification"][-1]["attempt_limit"] == min(2, item["budget"])


def test_failure_center_scope_error_never_opens_all_library(monkeypatch):
    from types import SimpleNamespace
    from PySide6.QtWidgets import QMessageBox
    from gui.widgets import recovery_controls as controls
    opened, warnings = [], []
    def fail():
        raise ValueError("范围快照失败")
    view = SimpleNamespace(_resolve_batch_snapshot=fail, service=None)
    monkeypatch.setattr(controls, "AnalysisFailureCenterDialog", lambda *a, **kw: opened.append(kw))
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[2]))
    controls.launch_failure_center(view)
    assert not opened
    assert warnings and "范围" in warnings[0]


@pytest.mark.parametrize("timing", ["before_run", "during_init"])
def test_worker_cancel_survives_analysis_initialization(service, tmp_path, monkeypatch, timing):
    from gui.workers.analysis_worker import AnalysisWorker
    path = video(service, tmp_path)
    worker = AnalysisWorker(service, [path], force_reanalyze=True)
    requests, results = [], []
    monkeypatch.setattr(service.ai, "analyze_video", lambda *a, **kw: requests.append(True))
    if timing == "before_run":
        worker.request_cancel()
    else:
        original = service._run_analysis
        def initialize(*a, **kw):
            worker.request_cancel()
            return original(*a, **kw)
        monkeypatch.setattr(service, "_run_analysis", initialize)
    worker.result_ready.connect(results.append)
    worker.run()
    assert requests == []
    assert results and results[0]["cancelled"]
    assert results[0]["cancelled_count"] == 1


def test_center_retry_starts_worker_and_close_requests_cancel(service, tmp_path, monkeypatch):
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtWidgets import QApplication, QDialog
    from gui.widgets.recovery_controls import AnalysisFailureCenterDialog, ManualRecoveryDialog
    app = QApplication.instance() or QApplication([])
    path = video(service, tmp_path)
    class Worker(QObject):
        progress_updated = Signal(int, str)
        task_finished = Signal(bool, str)
        result_ready = Signal(dict)
        def __init__(self, svc, paths, **kwargs):
            super().__init__()
            self.paths, self.kwargs = paths, kwargs
            self.running = False
            self.cancelled = False
        def start(self): self.running = True
        def isRunning(self): return self.running
        def request_cancel(self): self.cancelled = True
    def accept_preview(dialog):
        dialog.paths = (path,)
        dialog.options = {"recovery_mode": "primary"}
        return QDialog.Accepted
    monkeypatch.setattr(ManualRecoveryDialog, "exec", accept_preview)
    monkeypatch.setattr("gui.workers.analysis_worker.AnalysisWorker", Worker)
    dialog = AnalysisFailureCenterDialog(service, paths=[path])
    dialog.retry(paths=[path])
    assert dialog.worker.paths == [path]
    assert dialog.worker.kwargs["force_reanalyze"] is True
    assert not dialog.retry_btn.isEnabled()
    dialog.reject()
    assert dialog.worker.cancelled
    dialog.worker.running = False
    dialog.close()


def test_version_three_migration_failure_rolls_back_version_two(service, tmp_path, monkeypatch):
    from core.video_organizer_service import DatabaseManager
    with sqlite3.connect(service.db.db_path) as conn:
        conn.execute("PRAGMA user_version=2")
    def broken(conn):
        conn.execute("CREATE TABLE migration_probe(id INTEGER)")
        raise RuntimeError("migration failed")
    monkeypatch.setattr("core.analysis_failures.backfill", broken)
    with pytest.raises(RuntimeError, match="migration failed"):
        DatabaseManager(service.db.db_path)
    with sqlite3.connect(service.db.db_path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='migration_probe'").fetchone() is None


def test_first_non400_reanalysis_failure_retains_success(service, tmp_path, monkeypatch):
    path = video(service, tmp_path, status="analyzed")
    clients(service, monkeypatch, main=[HttpError(401, "unauthorized")])
    result = service.run_analysis([path], force_reanalyze=True)
    assert result["failed"] == 1
    row = service.db.get_video_by_path(path)
    assert row["status"] == "analyzed"
    assert row["summary"] == "旧摘要"
    assert service.query_analysis_failures(FailureQuery(retained_result=True))["total"] == 1


def test_manual_execution_cannot_exceed_preview_budget(service, tmp_path, monkeypatch):
    path = video(service, tmp_path)
    service.settings["processing"]["enable_audio_transcription"] = True
    service.settings["processing"]["http400_recovery"] = {"global": {"request_budget": 8, "primary_extra_attempts": 5, "delay_seconds": 0, "jitter": 0}, "tasks": {"content_description": {"request_budget": 2}}}
    preview = service.preview_manual_retry([path], manual_options={"request_interval": 0})
    assert preview["request_limit"] == 2
    # 即使预览后设置增大，本轮仍采用已确认的上限。
    service.settings["processing"]["http400_recovery"]["tasks"]["content_description"]["request_budget"] = 8
    cs = clients(service, monkeypatch, main=[HttpError(503, "unavailable")])
    result = service.run_analysis([path], recovery_mode="primary", manual_options=preview["manual_options"])
    assert result["failed"] == 1
    assert len(cs["main"].calls) == 2


def test_actions_and_paging_use_applied_filters(service, tmp_path):
    from PySide6.QtWidgets import QApplication
    from gui.widgets.recovery_controls import AnalysisFailureCenterDialog
    from core.analysis_recovery import effective_policy
    app = QApplication.instance() or QApplication([])
    paths = [video(service, tmp_path, name=name) for name in ("first.mp4", "second.mp4")]
    store = RecoveryStore(service.db)
    for path in paths:
        store.finish_failure(RecoveryContext(path, service.settings, effective_policy(service.settings)[0], store), "error")
    dialog = AnalysisFailureCenterDialog(service, paths)
    dialog.keyword.setText("first")
    assert set(dialog.selected_paths()) == set(paths)
    dialog.reload(reset=False)
    assert dialog.table.rowCount() == 2
    dialog.reload()
    assert dialog.selected_paths() == [paths[0]]
    dialog._receive_retry_result({"succeeded": 1, "failed": 2, "cancelled_count": 1, "skipped": 0, "remaining_failed_paths": [paths[1]]})
    dialog.round_only.setChecked(True)
    assert dialog.selected_paths() == [paths[1]]
    assert not dialog.keyword.isEnabled()
    dialog.close()


def test_diagnosis_modal_closes_before_opening_retry(service, monkeypatch):
    from PySide6.QtWidgets import QApplication, QDialog
    from gui.widgets.recovery_controls import AnalysisFailureCenterDialog
    app = QApplication.instance() or QApplication([])
    center = AnalysisFailureCenterDialog(service, [])
    result_dialog = QDialog(center)
    result_dialog.show()
    calls = []
    monkeypatch.setattr(center, "retry", lambda **kwargs: calls.append((result_dialog.isVisible(), kwargs)))
    center._apply_diagnosis(result_dialog, ("frozen.mp4",), {"concurrency": 1})
    assert not result_dialog.isVisible()
    assert calls == []
    app.processEvents()
    assert calls == [(False, {"paths": ("frozen.mp4",), "suggestion": {"concurrency": 1}})]
    center.close()


def test_analysis_worker_delivers_structured_result_before_completion():
    from gui.workers.analysis_worker import AnalysisWorker
    result = {"attempted": 3, "succeeded": 1, "failed": 2, "cancelled_count": 1, "remaining_failed_paths": ["failed.mp4"], "message": "done"}
    service = SimpleNamespace(run_analysis=lambda *args, **kwargs: result)
    worker = AnalysisWorker(service, ["one.mp4"])
    events = []
    worker.result_ready.connect(lambda payload: events.append(("result", payload)))
    worker.task_finished.connect(lambda ok, message: events.append(("finished", ok, message)))
    worker.run()
    assert events == [("result", result), ("finished", False, "done")]


def test_explicit_selection_does_not_expand_after_page_change(service, tmp_path):
    from PySide6.QtWidgets import QApplication
    from gui.widgets.recovery_controls import AnalysisFailureCenterDialog
    from core.analysis_recovery import effective_policy
    app = QApplication.instance() or QApplication([])
    paths = [video(service, tmp_path, name=f"clip{i}.mp4") for i in range(3)]
    store = RecoveryStore(service.db)
    for path in paths:
        store.finish_failure(RecoveryContext(path, service.settings, effective_policy(service.settings)[0], store), "error")
    dialog = AnalysisFailureCenterDialog(service, paths)
    dialog.page_size = 2; dialog.reload()
    dialog.action_scope.setCurrentIndex(0)
    dialog.table.selectRow(0)
    assert len(dialog.selected_paths()) == 1
    assert dialog.detail_btn.isEnabled()
    assert dialog.table.item(0, 0).toolTip() in paths
    dialog.change_page(1)
    assert dialog.selected_paths() == []
    assert dialog.action_scope.currentData() == "selected"
    dialog.action_scope.setCurrentIndex(1)
    assert set(dialog.selected_paths()) == set(paths)
    assert not dialog.advanced.isVisible()
    dialog.close()


def test_empty_last_page_returns_to_first_and_disables_navigation(service):
    from PySide6.QtWidgets import QApplication
    from gui.widgets.recovery_controls import AnalysisFailureCenterDialog
    app = QApplication.instance() or QApplication([])
    total = [101]
    calls = []
    def query(q, **kwargs):
        calls.append(kwargs["offset"])
        return {"rows": [], "total": total[0], "counts": {}}
    service.query_analysis_failures = query
    dialog = AnalysisFailureCenterDialog(service)
    dialog.change_page(1)
    total[0] = 0
    dialog.reload(reset=False)
    assert calls[-2:] == [100, 0]
    assert dialog.offset == 0
    assert not dialog.previous.isEnabled() and not dialog.next.isEnabled()
    assert "第 0 页" in dialog.page_label.text()
    assert "没有匹配" in dialog.empty_label.text()
    dialog.close()


def test_cancel_feedback_survives_progress_updates(service):
    from PySide6.QtWidgets import QApplication
    from gui.widgets.recovery_controls import AnalysisFailureCenterDialog
    app = QApplication.instance() or QApplication([])
    dialog = AnalysisFailureCenterDialog(service, [])
    calls = []
    dialog.worker = SimpleNamespace(request_cancel=lambda: calls.append(True), isRunning=lambda: False)
    dialog._cancel_retry()
    retry_text = dialog.summary.text()
    dialog._retry_progress(50, "normal progress")
    assert calls == [True] and dialog.summary.text() == retry_text
    assert not dialog.cancel_btn.isEnabled()
    dialog._cancel_diagnosis()
    diagnosis_text = dialog.summary.text()
    dialog._diagnosis_progress(1, 3)
    assert dialog.diagnosis_cancel.is_set() and dialog.summary.text() == diagnosis_text
    assert not dialog.cancel_diagnosis_btn.isEnabled()
    dialog.close()


def test_diagnosis_failure_counts_and_pre_cancelled_groups(service, monkeypatch):
    import threading
    groups = [{"fingerprint": str(i), "material_count": 1, "category": "unknown", "stage": "video_classification", "provider_id": "main", "model": "vision"} for i in range(2)]
    monkeypatch.setattr(FailureStore, "query", lambda *a, **kw: groups)
    monkeypatch.setattr(service.ai, "_get_api_response", lambda *a, **kw: None)
    progress = []
    result = service.diagnose_analysis_failures(on_progress=lambda *args: progress.append(args))
    assert (result["succeeded"], result["failed"], result["cancelled"]) == (0, 2, 0)
    assert progress == [(1, 2), (2, 2)]
    assert result["groups"][0]["model"] == "vision"
    event = threading.Event(); event.set()
    result = service.diagnose_analysis_failures(cancel_event=event)
    assert result["cancelled"] == 2 and result["groups"] == []


def test_diagnosis_buttons_stay_in_matching_group(service, monkeypatch):
    from PySide6.QtWidgets import QApplication, QDialog, QGroupBox, QPushButton, QLabel
    from gui.widgets.recovery_controls import AnalysisFailureCenterDialog
    app = QApplication.instance() or QApplication([])
    center = AnalysisFailureCenterDialog(service, [])
    groups = [{"fingerprint": str(i), "category": "unknown", "count": 1, "stage": "video_classification", "provider_id": f"provider{i}", "model": f"model{i}", "diagnosis": {"cause": f"cause{i}", "confidence": "未知", "retry_options": {"concurrency": 1}}} for i in range(2)]
    monkeypatch.setattr(FailureStore, "query", lambda *a, **kw: [kw.get("path", "target")])
    observed = []
    def inspect(dialog):
        cards = dialog.findChildren(QGroupBox)
        assert len(cards) == 2
        for index, card in enumerate(cards):
            assert f"第 {index + 1} 组" in card.title()
            assert f"provider{index}" in card.findChild(QLabel).text()
            assert f"cause{index}" in card.findChild(QLabel).text()
            assert f"第 {index + 1} 组" in card.findChild(QPushButton).text()
        observed.append(True)
        return QDialog.Rejected
    monkeypatch.setattr(QDialog, "exec", inspect)
    center._show_diagnosis_results({"groups": groups, "succeeded": 2}, FailureQuery(), ())
    assert observed == [True]
    center.close()


def test_workstation_failure_entry_requires_click_and_uses_round_scope(monkeypatch):
    from gui.views.workstation import WorkstationView
    import gui.views.workstation as module
    opened = []
    action = object()
    class Box:
        Warning = Close = ActionRole = 1
        choose = False
        def __init__(self, *args): pass
        def addButton(self, text, role):
            assert '2条' in text
            return action
        def exec(self): pass
        def clickedButton(self): return action if self.choose else None
        @staticmethod
        def information(*args): opened.append('information')
        @staticmethod
        def critical(*args): opened.append('critical')
    monkeypatch.setattr(module, 'QMessageBox', Box)
    monkeypatch.setattr('gui.widgets.recovery_controls.AnalysisFailureCenterDialog', lambda service, paths, parent: SimpleNamespace(exec=lambda: opened.append(tuple(paths))))
    control = SimpleNamespace(setEnabled=lambda *_: None, setVisible=lambda *_: None, setRange=lambda *_: None)
    view = SimpleNamespace(set_ui_enabled=lambda *_: None, cancel_analysis_btn=control, progress_bar=control, sub_progress_bar=control, progress_updated=SimpleNamespace(emit=lambda *_: None), status_label=SimpleNamespace(setText=lambda *_: None), model=SimpleNamespace(), service=SimpleNamespace(invalidate_video_memory_cache=lambda: None), load_data=lambda: None)
    result = {'remaining_failed_paths': ['first.mp4', 'second.mp4']}
    WorkstationView.on_task_finished(view, False, 'failed', analysis_result=result)
    assert opened == []
    Box.choose = True
    WorkstationView.on_task_finished(view, False, 'failed', analysis_result=result)
    assert opened == [('first.mp4', 'second.mp4')]
    WorkstationView.on_task_finished(view, True, 'rename done')
    assert opened[-1] == 'information'


def record_service_failure(service, path, message="service unavailable"):
    from core.analysis_recovery import effective_policy
    store = RecoveryStore(service.db)
    context = RecoveryContext(path, service.settings, effective_policy(service.settings)[0], store)
    context.last_error = {"status_code": 503, "message": message, "task_key": "video_classification"}
    store.finish_failure(context, message)


@pytest.mark.parametrize("invalid", [{}, {"cause": "限流"}, {"cause": " ", "evidence": "429", "actions": []}, {"cause": "限流", "evidence": "429", "actions": "降低并发"}])
def test_invalid_diagnosis_is_failed_and_not_cached(service, tmp_path, monkeypatch, invalid):
    path = video(service, tmp_path)
    record_service_failure(service, path)
    calls = []
    monkeypatch.setattr(service.ai, "_get_api_response", lambda *a, **kw: calls.append(True) or invalid)
    for _ in range(2):
        result = service.diagnose_analysis_failures(paths=[path])
        assert result["failed"] == 1 and result["succeeded"] == 0
    assert len(calls) == 2
    assert service.db.execute_query("SELECT * FROM analysis_failure_diagnoses") == []


def test_old_invalid_cache_is_ignored_and_force_refreshes_valid_cache(service, tmp_path, monkeypatch):
    from core.analysis_failures import route_fingerprint
    path = video(service, tmp_path)
    record_service_failure(service, path)
    store = FailureStore(service.db)
    fingerprint = store.query()["rows"][0]["fingerprint"]
    route = service.ai.resolve_task_route("error_diagnosis")
    route_hash = route_fingerprint({k: route.get(k, "") for k in ("provider_id", "model", "base_url")})
    store.save_diagnosis(fingerprint, route_hash, {"cause": "未能确定原因", "evidence": "", "actions": [], "retry_options": {}})
    calls = []
    def diagnose(*args, **kwargs):
        calls.append(True)
        return {"cause": f"cause{len(calls)}", "evidence": "503", "actions": ["稍后重试"], "retry_options": {}}
    monkeypatch.setattr(service.ai, "_get_api_response", diagnose)
    assert service.diagnose_analysis_failures()["groups"][0]["diagnosis"]["cause"] == "cause1"
    assert service.diagnose_analysis_failures()["groups"][0]["diagnosis"]["cause"] == "cause1"
    assert service.diagnose_analysis_failures(force=True)["groups"][0]["diagnosis"]["cause"] == "cause2"
    assert len(calls) == 2


def test_invalid_suggestion_keeps_results_open(service, monkeypatch):
    from PySide6.QtWidgets import QApplication, QDialog, QMessageBox
    from gui.widgets.recovery_controls import AnalysisFailureCenterDialog
    app = QApplication.instance() or QApplication([])
    center = AnalysisFailureCenterDialog(service, [])
    results = QDialog(center); results.show()
    calls = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: calls.append("warning"))
    monkeypatch.setattr(center, "retry", lambda **kwargs: calls.append("retry"))
    center._apply_diagnosis(results, ("test.mp4",), {"concurrency": 999})
    app.processEvents()
    assert results.isVisible() and calls == ["warning"]
    results.close(); center.close()


def test_refresh_diagnosis_preserves_scope_and_requires_new_preview(service, monkeypatch):
    from PySide6.QtWidgets import QApplication, QDialog
    from gui.widgets.recovery_controls import AnalysisFailureCenterDialog
    app = QApplication.instance() or QApplication([])
    center = AnalysisFailureCenterDialog(service, [])
    results = QDialog(center); results.show()
    calls = []
    monkeypatch.setattr(center, "diagnose", lambda **kwargs: calls.append(kwargs))
    query = FailureQuery(category="service")
    center._restart_diagnosis(results, query, ("frozen.mp4",))
    assert not results.isVisible() and calls == []
    app.processEvents()
    assert calls == [{"query": query, "paths": ("frozen.mp4",), "force": True}]
    center.close()


def test_date_end_includes_whole_day_and_respects_timezones(service, tmp_path):
    paths = [video(service, tmp_path, name=f"day{i}.mp4") for i in range(4)]
    timestamps = ["2026-11-09T00:00:00+00:00", "2026-11-09T23:59:59.900000+00:00", "2026-11-10T00:00:00+00:00", "2026-11-10T07:59:59+08:00"]
    for path, stamp in zip(paths, timestamps):
        record_service_failure(service, path)
        service.db.execute_non_query("UPDATE video_recovery SET updated_at=? WHERE video_id=(SELECT id FROM videos WHERE path=?)", (stamp, path))
    query = FailureQuery(since="2026-11-09", until="2026-11-09")
    result = service.query_analysis_failures(query)
    assert {row["path"] for row in result["rows"]} == {paths[0], paths[1], paths[3]}
    assert result["total"] == sum(result["counts"].values()) == 3
    assert set(FailureStore(service.db).query(query, paths_only=True)) == {paths[0], paths[1], paths[3]}
    exact = FailureQuery(since="2026-11-09T08:00:00+08:00", until="2026-11-09T00:00:00Z")
    assert service.query_analysis_failures(exact)["total"] == 1


@pytest.mark.parametrize("since,until", [("bad", ""), ("", "2026-02-30"), ("2026-11-10", "2026-11-09"), ("2026-11-09T01:00:00Z", "2026-11-09T00:00:00Z")])
def test_invalid_time_filters_are_rejected(service, since, until):
    with pytest.raises(ValueError):
        service.query_analysis_failures(FailureQuery(since=since, until=until))


def test_invalid_ui_filter_preserves_applied_query_and_page(service, monkeypatch):
    from PySide6.QtWidgets import QApplication, QMessageBox
    from gui.widgets.recovery_controls import AnalysisFailureCenterDialog
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(service, "query_analysis_failures", lambda *a, **kw: {"rows": [], "total": 101, "counts": {}})
    center = AnalysisFailureCenterDialog(service)
    center.change_page(1)
    old_query = center.applied_query
    messages = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: messages.append(args[2]))
    center.until.setText("2026-02-30"); center.reload()
    assert messages and center.applied_query == old_query and center.offset == 100
    center.close()


def test_generic_recovered_state_and_history_full_text(service, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication, QDialog, QPlainTextEdit, QTableWidget
    from gui.widgets.recovery_controls import AnalysisFailureCenterDialog, show_recovery_detail
    app = QApplication.instance() or QApplication([])
    path = video(service, tmp_path)
    record_service_failure(service, path)
    service.db.execute_non_query("UPDATE video_recovery SET state='recovered'")
    center = AnalysisFailureCenterDialog(service, [path])
    center.state.setCurrentIndex(center.state.findData("recovered"))
    assert center.table.item(0, 4).text() == "已恢复"
    detail = service.get_recovery_detail(path)
    assert detail["failure"]["ever400"] == 0
    message = "historical failure " * 50
    detail["attempts"] = [{"created_at": "2026-11-09", "detail": {"message": message}}, {"created_at": "2026-11-08", "detail": {"message": "second error"}}]
    monkeypatch.setattr(service, "get_recovery_detail", lambda path: detail)
    inspected = []
    def inspect(dialog):
        current = dialog.findChild(QPlainTextEdit, "failure_details").toPlainText()
        assert "服务不可用" in current and "本地判断依据：HTTP 503" in current
        assert "曾遇到400：否" in current and "曾遇到400，已恢复" not in current
        reader = dialog.findChild(QPlainTextEdit, "history_full_text")
        assert reader.isReadOnly() and message in reader.toPlainText()
        table = dialog.findChild(QTableWidget)
        assert table.item(0, 5).toolTip() == message
        table.setCurrentCell(1, 0)
        assert "second error" in reader.toPlainText() and message not in reader.toPlainText()
        inspected.append(True)
        return QDialog.Rejected
    monkeypatch.setattr(QDialog, "exec", inspect)
    show_recovery_detail(service, path, center)
    assert inspected == [True]
    center.close()


def test_cancel_status_survives_refresh_paging_and_filters(service, monkeypatch):
    from PySide6.QtWidgets import QApplication
    from gui.widgets.recovery_controls import AnalysisFailureCenterDialog
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(service, "query_analysis_failures", lambda *a, **kw: {"rows": [], "total": 101, "counts": {}})
    center = AnalysisFailureCenterDialog(service)
    center.worker = SimpleNamespace(request_cancel=lambda: None, isRunning=lambda: True)
    center._cancel_retry()
    message = center.summary.text()
    center.reload(); center.change_page(1); center.ever400.setChecked(True)
    assert center.summary.text() == message and "101" in center.list_summary.text()
    center._cancel_diagnosis(); message = center.summary.text()
    center.reload()
    assert center.summary.text() == message
    center.worker = None; center.close()


def test_retry_failed_diagnosis_button_only_targets_failed_group(service, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication, QDialog, QPushButton
    from gui.widgets.recovery_controls import AnalysisFailureCenterDialog
    app = QApplication.instance() or QApplication([])
    paths = [video(service, tmp_path, name=f"diagnosis{i}.mp4") for i in range(2)]
    for index, path in enumerate(paths):
        record_service_failure(service, path, message=f"unavailable group {chr(65 + index)}")
    rows = FailureStore(service.db).query()["rows"]
    result = {"groups": [{"fingerprint": row["fingerprint"], "count": 1, "category": row["category"], "failed": row["path"] == paths[1], "diagnosis": {"cause": "failed", "actions": []}} for row in rows]}
    center = AnalysisFailureCenterDialog(service, paths)
    calls = []
    monkeypatch.setattr(center, "_restart_diagnosis", lambda dialog, query, targets: calls.append(tuple(targets)))
    def inspect(dialog):
        button = next(button for button in dialog.findChildren(QPushButton) if button.text().startswith("仅重试诊断失败组"))
        button.click()
        return QDialog.Rejected
    monkeypatch.setattr(QDialog, "exec", inspect)
    center._show_diagnosis_results(result, FailureQuery(), tuple(paths))
    assert calls == [(paths[1],)]
    center.close()
