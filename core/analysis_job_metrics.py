# -*- coding: utf-8 -*-
"""分析任务运行时指标：调用/重试/缓存命中（线程安全，可落盘）。"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


def _safe_rate(num: int, den: int) -> float:
    if den <= 0:
        return 0.0
    return round(100.0 * float(num) / float(den), 2)


@dataclass
class AnalysisJobMetrics:
    """单次分析任务的累计计数。"""

    job_id: str
    started_at: float
    force_reanalyze: bool = False
    target_count: int = 0
    max_workers: int = 0

    # API 调用层
    api_attempts: int = 0  # 每一次 chat.completions 请求
    api_success: int = 0
    api_fail: int = 0
    call_retries: int = 0  # attempt >= 2 的次数（额外重试发生次数）

    # 缓存
    cache_hits: int = 0
    cache_misses: int = 0

    # 单条层
    item_starts: int = 0  # 进入 _process_single_video
    item_attempts: int = 0  # 单条完整尝试（含 B 重试）
    item_retries: int = 0  # attempt_b >= 2
    item_success: int = 0
    item_fail: int = 0
    extract_fail: int = 0

    # 批次
    batch_rerun_started: int = 0
    cancelled: bool = False

    # 事件样例（最多保留 N 条，避免内存爆）
    recent_events: List[Dict[str, Any]] = field(default_factory=list)
    _max_events: int = 40

    def rates(self) -> Dict[str, float]:
        """可解释的比率（百分比）。"""
        # 调用层重试率 = 额外重试次数 / 首次请求次数
        # 首次请求约 = api_success + api_fail 中「逻辑请求」≈ cache_misses 触发的请求链
        # 更直观：call_retries / max(api_attempts - call_retries, 1) = 额外重试/初试
        first_tries = max(self.api_attempts - self.call_retries, 0)
        return {
            "call_retry_rate_pct": _safe_rate(self.call_retries, first_tries),
            "api_fail_rate_pct": _safe_rate(self.api_fail, self.api_attempts),
            "cache_hit_rate_pct": _safe_rate(
                self.cache_hits, self.cache_hits + self.cache_misses
            ),
            "item_retry_rate_pct": _safe_rate(
                self.item_retries, max(self.item_starts, 1)
            ),
            "item_fail_rate_pct": _safe_rate(
                self.item_fail, self.item_success + self.item_fail
            ),
            "avg_api_attempts_per_item": round(
                self.api_attempts / float(max(self.item_starts, 1)), 3
            ),
        }

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d.pop("_max_events", None)
        d["elapsed_sec"] = round(time.time() - self.started_at, 2)
        d["rates"] = self.rates()
        d["finished_at"] = datetime.now().isoformat(timespec="seconds")
        return d

    def summary_line(self) -> str:
        r = self.rates()
        return (
            f"任务 {self.job_id} | "
            f"API尝试 {self.api_attempts} (成功{self.api_success}/失败{self.api_fail}) | "
            f"调用层重试 {self.call_retries} ({r['call_retry_rate_pct']}%) | "
            f"缓存 命中{self.cache_hits}/未命中{self.cache_misses} "
            f"({r['cache_hit_rate_pct']}%) | "
            f"单条 开始{self.item_starts} 重试{self.item_retries} "
            f"成功{self.item_success}/失败{self.item_fail} "
            f"(单条重试率{r['item_retry_rate_pct']}%) | "
            f"均API/条 {r['avg_api_attempts_per_item']} | "
            f"补跑轮 {self.batch_rerun_started} | "
            f"耗时 {round(time.time() - self.started_at, 1)}s"
        )


class AnalysisMetricsTracker:
    """线程安全的指标收集器；绑定到一次 run_analysis。"""

    def __init__(self, metrics: AnalysisJobMetrics):
        self._m = metrics
        self._lock = threading.Lock()

    @property
    def job_id(self) -> str:
        return self._m.job_id

    def _event(self, kind: str, **kwargs: Any) -> None:
        ev = {
            "t": datetime.now().isoformat(timespec="milliseconds"),
            "kind": kind,
            **kwargs,
        }
        with self._lock:
            self._m.recent_events.append(ev)
            if len(self._m.recent_events) > self._m._max_events:
                self._m.recent_events = self._m.recent_events[-self._m._max_events :]

    def record_api_attempt(
        self,
        *,
        attempt: int,
        model: str = "",
        task_key: str = "",
        ok: bool,
        error: str = "",
        retriable: Optional[bool] = None,
    ) -> None:
        with self._lock:
            self._m.api_attempts += 1
            if attempt >= 2:
                self._m.call_retries += 1
            if ok:
                self._m.api_success += 1
            else:
                self._m.api_fail += 1
        self._event(
            "api_attempt",
            attempt=attempt,
            model=model,
            task_key=task_key,
            ok=ok,
            error=(error or "")[:160],
            retriable=retriable,
        )

    def record_cache(self, hit: bool, key: str = "") -> None:
        with self._lock:
            if hit:
                self._m.cache_hits += 1
            else:
                self._m.cache_misses += 1
        self._event("cache", hit=hit, key=(key or "")[:80])

    def record_item_start(self, path: str = "") -> None:
        with self._lock:
            self._m.item_starts += 1
        self._event("item_start", path=os.path.basename(path) if path else "")

    def record_item_attempt(self, attempt_b: int, path: str = "") -> None:
        with self._lock:
            self._m.item_attempts += 1
            if attempt_b >= 2:
                self._m.item_retries += 1
        self._event(
            "item_attempt",
            attempt_b=attempt_b,
            path=os.path.basename(path) if path else "",
        )

    def record_item_result(
        self, *, ok: bool, path: str = "", reason: str = "", extract_fail: bool = False
    ) -> None:
        with self._lock:
            if ok:
                self._m.item_success += 1
            else:
                self._m.item_fail += 1
            if extract_fail:
                self._m.extract_fail += 1
        self._event(
            "item_result",
            ok=ok,
            path=os.path.basename(path) if path else "",
            reason=(reason or "")[:160],
            extract_fail=extract_fail,
        )

    def record_batch_rerun(self) -> None:
        with self._lock:
            self._m.batch_rerun_started += 1
        self._event("batch_rerun")

    def set_cancelled(self) -> None:
        with self._lock:
            self._m.cancelled = True
        self._event("cancelled")

    def counters_snapshot(self) -> Dict[str, Any]:
        """轻量计数快照（不含 recent_events，供高频 UI 节流推送）。"""
        with self._lock:
            m = self._m
            return {
                "job_id": m.job_id,
                "api_attempts": m.api_attempts,
                "call_retries": m.call_retries,
                "cache_hits": m.cache_hits,
                "cache_misses": m.cache_misses,
                "item_retries": m.item_retries,
                "item_success": m.item_success,
                "item_fail": m.item_fail,
                "rates": m.rates(),
            }

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return self._m.to_dict()

    def summary_line(self) -> str:
        with self._lock:
            return self._m.summary_line()


def new_job_metrics(
    *,
    force_reanalyze: bool = False,
    target_count: int = 0,
    max_workers: int = 0,
) -> AnalysisMetricsTracker:
    mid = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
    m = AnalysisJobMetrics(
        job_id=mid,
        started_at=time.time(),
        force_reanalyze=force_reanalyze,
        target_count=target_count,
        max_workers=max_workers,
    )
    return AnalysisMetricsTracker(m)


def default_log_dir() -> str:
    base = os.path.abspath(".")
    return os.path.join(base, "logs", "analysis_jobs")


def persist_job_metrics(
    tracker: AnalysisMetricsTracker,
    *,
    log_dir: Optional[str] = None,
) -> str:
    """写入 JSON 汇总 + 追加 JSONL 事件；返回主 JSON 路径。"""
    d = log_dir or default_log_dir()
    os.makedirs(d, exist_ok=True)
    snap = tracker.snapshot()
    job_id = snap.get("job_id") or "unknown"
    json_path = os.path.join(d, f"job_{job_id}.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=2)
    # 滚动总表
    index_path = os.path.join(d, "jobs_index.jsonl")
    slim = {
        "job_id": job_id,
        "finished_at": snap.get("finished_at"),
        "elapsed_sec": snap.get("elapsed_sec"),
        "target_count": snap.get("target_count"),
        "api_attempts": snap.get("api_attempts"),
        "call_retries": snap.get("call_retries"),
        "cache_hits": snap.get("cache_hits"),
        "cache_misses": snap.get("cache_misses"),
        "item_success": snap.get("item_success"),
        "item_fail": snap.get("item_fail"),
        "item_retries": snap.get("item_retries"),
        "rates": snap.get("rates"),
        "cancelled": snap.get("cancelled"),
        "force_reanalyze": snap.get("force_reanalyze"),
    }
    with open(index_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(slim, ensure_ascii=False) + "\n")
    return json_path


def setup_analysis_file_logging(log_dir: Optional[str] = None) -> str:
    """为 VideoOrganizer logger 增加 analysis 专用滚动文件（幂等）。"""
    import logging
    from logging.handlers import RotatingFileHandler

    d = log_dir or default_log_dir()
    os.makedirs(d, exist_ok=True)
    log_path = os.path.join(d, "analysis.log")
    root = logging.getLogger("VideoOrganizer")
    # 避免重复挂 handler
    for h in root.handlers:
        if getattr(h, "_analysis_job_file", False):
            return log_path
    fh = RotatingFileHandler(
        log_path, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    fh.setLevel(logging.INFO)
    fh.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    fh._analysis_job_file = True  # type: ignore[attr-defined]
    root.addHandler(fh)
    root.setLevel(logging.INFO)
    return log_path