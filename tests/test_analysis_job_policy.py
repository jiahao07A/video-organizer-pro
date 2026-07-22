# -*- coding: utf-8 -*-
"""分析任务策略纯逻辑（ticket 01）。"""
import json

from core.analysis_job_policy import (
    DEFAULT_CALL_EXTRA_ATTEMPTS,
    DEFAULT_ITEM_MAX_ATTEMPTS,
    AnalysisPhase,
    ProgressReducer,
    RetryConfig,
    RoundKind,
    backoff_seconds,
    is_retriable,
    should_batch_rerun,
    should_retry_call,
    should_retry_item,
)


def test_is_retriable_timeout_and_http():
    assert is_retriable(TimeoutError("timeout"))
    assert is_retriable("Connection reset by peer")
    assert is_retriable("HTTP 429 rate limit")
    assert is_retriable({"status_code": 503, "message": "bad gateway"})
    assert is_retriable(json.JSONDecodeError("Expecting value", "", 0))
    assert is_retriable("Expecting value: line 1 column 1")


def test_is_retriable_non_retriable():
    assert not is_retriable({"status_code": 401, "message": "Unauthorized"})
    assert not is_retriable("403 Forbidden")
    assert not is_retriable("用户取消")
    assert not is_retriable("文件不存在: x.mp4")
    assert not is_retriable("无法打开视频")
    assert not is_retriable(None)


def test_should_retry_call_bounds():
    # extra=2 → 最多 3 次
    assert should_retry_call(1, call_extra_attempts=2) is True
    assert should_retry_call(2, call_extra_attempts=2) is True
    assert should_retry_call(3, call_extra_attempts=2) is False
    assert should_retry_call(1, call_extra_attempts=0) is False


def test_should_retry_item_bounds():
    assert should_retry_item(1, item_max_attempts=2) is True
    assert should_retry_item(2, item_max_attempts=2) is False
    assert should_retry_item(1, item_max_attempts=1) is False


def test_should_batch_rerun_matrix():
    assert should_batch_rerun(3, rerun_done=False, enabled=True, cancelled=False) is True
    assert should_batch_rerun(0, rerun_done=False, enabled=True, cancelled=False) is False
    assert should_batch_rerun(2, rerun_done=True, enabled=True, cancelled=False) is False
    assert should_batch_rerun(2, rerun_done=False, enabled=False, cancelled=False) is False
    assert should_batch_rerun(2, rerun_done=False, enabled=True, cancelled=True) is False


def test_backoff_injectable():
    assert backoff_seconds(1, base=1.0, cap=8.0) == 1.0
    assert backoff_seconds(2, base=1.0, cap=8.0) == 2.0
    assert backoff_seconds(4, base=1.0, cap=8.0) == 8.0  # capped
    slept = []
    from core.analysis_job_policy import sleep_backoff

    d = sleep_backoff(1, config=RetryConfig(backoff_base_sec=1.0), sleeper=slept.append)
    assert d == 1.0
    assert slept == [1.0]


def test_progress_first_round_denominator_and_no_b_inflate():
    r = ProgressReducer()
    snap = r.apply("job_started", target_count=10)
    assert snap.overall_total == 10
    assert snap.sub_total == 10
    r.apply("round_started", kind=RoundKind.FIRST, item_count=10)
    r.apply("item_phase", path="a.mp4", phase=AnalysisPhase.AI, attempt_b=1)
    r.apply("item_phase", path="a.mp4", phase=AnalysisPhase.AI, attempt_b=2)  # B 重试
    # 分母不变
    assert r.snapshot().sub_total == 10
    assert r.snapshot().overall_total == 10
    r.apply("item_terminal", path="a.mp4", ok=True)
    s = r.snapshot()
    assert s.overall_done == 1
    assert s.sub_done == 1
    assert any("成功" in v for v in s.rows.values())


def test_progress_rerun_resets_sub_keeps_overall():
    r = ProgressReducer()
    r.apply("job_started", target_count=5)
    r.apply("round_started", kind="first", item_count=5)
    for i, p in enumerate(["a", "b", "c", "d", "e"]):
        ok = i < 3
        r.apply("item_terminal", path=p, ok=ok)
    s1 = r.snapshot()
    assert s1.overall_done == 5
    assert s1.sub_done == 5
    # 补跑 2 失败
    r.apply("round_started", kind=RoundKind.RERUN, item_count=2)
    s2 = r.snapshot()
    assert s2.overall_done == 5  # 不清零
    assert s2.sub_done == 0
    assert s2.sub_total == 2
    assert "补跑" in s2.round_label or "补跑" in s2.message
    r.apply("item_terminal", path="d", ok=True)
    s3 = r.snapshot()
    assert s3.sub_done == 1
    assert s3.overall_done == 5  # 已都是终态
    assert any(v == "成功" for k, v in s3.rows.items() if "d" in k or k.endswith("d"))


def test_progress_cancel_flag():
    r = ProgressReducer()
    r.apply("job_started", target_count=2)
    r.apply("job_cancelled")
    assert r.snapshot().cancelled is True


def test_retry_config_from_settings():
    cfg = RetryConfig.from_settings(
        {
            "processing": {
                "analysis_retry": {
                    "call_extra_attempts": 1,
                    "item_max_attempts": 3,
                    "batch_rerun_enabled": False,
                }
            }
        }
    )
    assert cfg.call_extra_attempts == 1
    assert cfg.item_max_attempts == 3
    assert cfg.batch_rerun_enabled is False
    assert RetryConfig.from_settings(None).call_extra_attempts == DEFAULT_CALL_EXTRA_ATTEMPTS
    assert RetryConfig.from_settings({}).item_max_attempts == DEFAULT_ITEM_MAX_ATTEMPTS


def test_call_and_item_counters_independent():
    # 调用层用尽 ≠ 单条层用尽
    assert should_retry_call(3, call_extra_attempts=2) is False
    assert should_retry_item(1, item_max_attempts=2) is True