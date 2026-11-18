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


class ResourceSampler:
    """低频采样进程资源，只汇总峰值，不记录素材内容。"""
    def __init__(self, tracker, interval=1.0):
        self.tracker = tracker
        self.interval = interval
        self._stop = threading.Event()
        self._thread = None
        self._io_start = (0, 0)

    def start(self):
        try:
            import psutil
            self._process = psutil.Process()
            self._process.cpu_percent(None)
            initial_io = self._process.io_counters()
            self._io_start = (initial_io.read_bytes, initial_io.write_bytes)
        except Exception as exc:
            self._process = None
            self.tracker.set_resource_sampling('unavailable', type(exc).__name__)
            return self
        self._thread = threading.Thread(target=self._run, name="analysis-resource-sampler", daemon=True)
        self._thread.start()
        return self

    def _run(self):
        while not self._stop.wait(self.interval):
            try:
                memory = self._process.memory_info()
                io = self._process.io_counters()
                self.tracker.record_resource_peak(
                    cpu_percent=self._process.cpu_percent(None),
                    rss_mb=memory.rss / 1024**2,
                    threads=self._process.num_threads(),
                    read_mb=max(0, io.read_bytes - self._io_start[0]) / 1024**2,
                    write_mb=max(0, io.write_bytes - self._io_start[1]) / 1024**2,
                )
            except Exception as exc:
                self.tracker.set_resource_sampling('unavailable', type(exc).__name__)
                self._stop.set()
                return

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join()
            self._thread = None


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
    extract_parallel: int = 0
    phase_elapsed_sec: Dict[str, Any] = field(default_factory=dict)
    resource_peak: Dict[str, float] = field(default_factory=dict)
    resource_sampling: Dict[str, str] = field(default_factory=lambda: {'status': 'available', 'reason': ''})

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

    def record_phase(self, name: str, elapsed_sec: float) -> None:
        with self._lock:
            elapsed = max(0.0, elapsed_sec)
            phase = self._m.phase_elapsed_sec.setdefault(name, {
                "count": 0, "total_sec": 0.0, "max_sec": 0.0, "buckets": {},
            })
            phase["count"] += 1
            phase["total_sec"] = round(phase["total_sec"] + elapsed, 3)
            phase["max_sec"] = round(max(phase["max_sec"], elapsed), 3)
            bucket = "<1s" if elapsed < 1 else "1-5s" if elapsed < 5 else "5-15s" if elapsed < 15 else "15-60s" if elapsed < 60 else ">=60s"
            phase["buckets"][bucket] = phase["buckets"].get(bucket, 0) + 1

    def record_resource_peak(self, **values: float) -> None:
        with self._lock:
            for key, value in values.items():
                if isinstance(value, (int, float)):
                    self._m.resource_peak[key] = max(float(value), self._m.resource_peak.get(key, 0.0))

    def set_resource_sampling(self, status: str, reason: str = '') -> None:
        with self._lock:
            self._m.resource_sampling = {'status': status, 'reason': reason[:80]}

    @property
    def job_id(self) -> str:
        return self._m.job_id

    def _event(self, kind: str, **kwargs: Any) -> None:
        from core.diagnostic_logging import event, register_private_path, sanitize
        if kwargs.get('path'):
            kwargs['item_id'] = register_private_path(kwargs.pop('path'))
        kwargs = sanitize(kwargs)
        event('analysis.' + kind, job_id=self.job_id, **kwargs)
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
        self._event("item_start", path=path if path else "")

    def record_item_attempt(self, attempt_b: int, path: str = "") -> None:
        with self._lock:
            self._m.item_attempts += 1
            if attempt_b >= 2:
                self._m.item_retries += 1
        self._event(
            "item_attempt",
            attempt_b=attempt_b,
            path=path if path else "",
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
            path=path if path else "",
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
    extract_parallel: int = 0,
    started_at: Optional[float] = None,
) -> AnalysisMetricsTracker:
    mid = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
    m = AnalysisJobMetrics(
        job_id=mid,
        started_at=time.time() if started_at is None else started_at,
        force_reanalyze=force_reanalyze,
        target_count=target_count,
        max_workers=max_workers,
        extract_parallel=extract_parallel,
    )
    return AnalysisMetricsTracker(m)


def default_log_dir() -> str:
    """返回分析诊断目录，支持人工测试会话覆盖默认位置。"""
    configured = os.environ.get("VIDEO_ORGANIZER_ANALYSIS_LOG_DIR")
    if configured:
        return os.path.abspath(configured)
    from core.diagnostic_logging import get_runtime
    runtime = get_runtime()
    if runtime:
        return str(runtime.directory / 'analysis_jobs')
    from core.data_directory import DataDirectoryManager
    return os.path.join(DataDirectoryManager().resolve_active_paths().root, 'logs', 'analysis_jobs')


def persist_job_metrics(
    tracker: AnalysisMetricsTracker,
    *,
    log_dir: Optional[str] = None,
) -> str:
    """写入 JSON 汇总 + 追加 JSONL 事件；返回主 JSON 路径。"""
    d = log_dir or default_log_dir()
    os.makedirs(d, exist_ok=True)
    from core.diagnostic_logging import sanitize, get_runtime
    snap = sanitize(tracker.snapshot())
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
    if os.path.exists(index_path) and os.path.getsize(index_path) >= 1024 * 1024:
        os.replace(index_path, index_path + '.1')
    with open(index_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(slim, ensure_ascii=False) + "\n")
    runtime = get_runtime()
    if runtime:
        if runtime.lock.acquire(timeout=0.05):
            try:
                runtime.prune()
            finally:
                runtime.lock.release()
        else:
            runtime.warning = '日志清理繁忙，分析指标已保存，清理延后执行。'
    return json_path


def setup_analysis_file_logging(log_dir: Optional[str] = None) -> str:
    """为 VideoOrganizer logger 增加 analysis 专用滚动文件（幂等）。"""
    from core.diagnostic_logging import initialize, get_runtime
    if not log_dir:
        runtime = get_runtime() or initialize()
        return str(runtime.directory / f'events-{runtime.session_id}.jsonl')
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
    from core.diagnostic_logging import sanitize
    class SafeFormatter(logging.Formatter):
        def format(self, record):
            return str(sanitize(super().format(record)))
    fh.setFormatter(
        SafeFormatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    fh._analysis_job_file = True  # type: ignore[attr-defined]
    root.addHandler(fh)
    root.setLevel(logging.DEBUG)
    return log_path
