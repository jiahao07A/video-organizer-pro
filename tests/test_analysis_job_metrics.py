# -*- coding: utf-8 -*-
"""分析任务指标：重试率与缓存命中。"""
import json
from pathlib import Path
from core.analysis_job_metrics import (
    AnalysisJobMetrics,
    new_job_metrics,
    persist_job_metrics,
)


def test_rates_zero_safe():
    m = AnalysisJobMetrics(job_id="t", started_at=0.0)
    r = m.rates()
    assert r["call_retry_rate_pct"] == 0.0
    assert r["cache_hit_rate_pct"] == 0.0


def test_call_retry_rate():
    tr = new_job_metrics(target_count=2)
    # 第一次请求 attempt=1 失败，attempt=2 成功 → 1 次额外重试
    tr.record_api_attempt(attempt=1, ok=False, error="timeout", retriable=True)
    tr.record_api_attempt(attempt=2, ok=True)
    tr.record_cache(False, key="k1")
    tr.record_item_start("a.mp4")
    tr.record_item_attempt(1, "a.mp4")
    tr.record_item_result(ok=True, path="a.mp4")
    snap = tr.snapshot()
    assert snap["api_attempts"] == 2
    assert snap["call_retries"] == 1
    assert snap["api_success"] == 1
    assert snap["api_fail"] == 1
    # first_tries = 2-1 = 1 → retry rate 100%
    assert snap["rates"]["call_retry_rate_pct"] == 100.0
    assert snap["rates"]["avg_api_attempts_per_item"] == 2.0


def test_item_retry_and_cache_hit_rate():
    tr = new_job_metrics()
    tr.record_cache(True)
    tr.record_cache(True)
    tr.record_cache(False)
    tr.record_item_start("x")
    tr.record_item_attempt(1, "x")
    tr.record_item_attempt(2, "x")  # 单条重试
    tr.record_item_result(ok=True, path="x")
    r = tr.snapshot()["rates"]
    assert r["cache_hit_rate_pct"] == round(100 * 2 / 3, 2)
    assert r["item_retry_rate_pct"] == 100.0  # 1 start, 1 retry event


def test_persist_job_metrics(tmp_path):
    tr = new_job_metrics(target_count=1)
    tr.record_api_attempt(attempt=1, ok=True)
    tr.record_item_start("a")
    tr.record_item_result(ok=True, path="a")
    path = persist_job_metrics(tr, log_dir=str(tmp_path))
    saved = json.loads(Path(path).read_text(encoding="utf-8"))
    expected = tr.snapshot()
    assert {k: v for k, v in saved.items() if k not in ("elapsed_sec", "finished_at")} == {k: v for k, v in expected.items() if k not in ("elapsed_sec", "finished_at")}
    assert saved["api_attempts"] == saved["item_success"] == 1
    first_index = (tmp_path / "jobs_index.jsonl").read_text(encoding="utf-8")
    second = new_job_metrics(target_count=2)
    second_path = persist_job_metrics(second, log_dir=str(tmp_path))
    second_saved = json.loads(Path(second_path).read_text(encoding="utf-8"))
    assert second_saved["job_id"] == second.snapshot()["job_id"]
    assert second_saved["target_count"] == 2
    assert second_saved["api_attempts"] == second_saved["item_success"] == 0
    index = (tmp_path / "jobs_index.jsonl").read_text(encoding="utf-8")
    assert index.startswith(first_index)
    records = [json.loads(line) for line in index.splitlines()]
    assert [r["job_id"] for r in records] == [saved["job_id"], second.snapshot()["job_id"]]
    assert records[0]["api_attempts"] == records[0]["item_success"] == 1
    assert records[1]["target_count"] == 2


def test_summary_line_contains_key_fields():
    tr = new_job_metrics()
    tr.record_api_attempt(attempt=1, ok=True)
    line = tr.summary_line()
    assert "API尝试" in line
    assert "调用层重试" in line
