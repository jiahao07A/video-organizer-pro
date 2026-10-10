# -*- coding: utf-8 -*-
"""分析任务策略：调用层/单条层/批次补跑重试 + 进度事件归约（纯逻辑，可单测）。

产品词见 CONTEXT.md / ADR-0005。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Union


# --- 默认上限（P） ---
DEFAULT_CALL_EXTRA_ATTEMPTS = 2  # 额外次数 → 共 3 次尝试
DEFAULT_ITEM_MAX_ATTEMPTS = 2  # 单条完整尝试次数
DEFAULT_BATCH_RERUN_ENABLED = True
DEFAULT_BATCH_RERUN_MAX_ROUNDS = 1

# 退避：1s → 2s → 4s …
DEFAULT_BACKOFF_BASE_SEC = 1.0
DEFAULT_BACKOFF_CAP_SEC = 8.0


class AnalysisPhase(str, Enum):
    EXTRACT = "extract"  # 抽帧
    AI = "ai"  # 调 AI
    NORMALIZE = "normalize"  # 归一/落库准备
    XMP = "xmp"  # 侧车
    IDLE = "idle"


class RowTempStatus(str, Enum):
    ANALYZING = "analyzing"
    RETRYING = "retrying"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RoundKind(str, Enum):
    FIRST = "first"
    RERUN = "rerun"


PHASE_LABELS = {
    AnalysisPhase.EXTRACT: "抽帧",
    AnalysisPhase.AI: "调 AI",
    AnalysisPhase.NORMALIZE: "归一落库",
    AnalysisPhase.XMP: "XMP",
    AnalysisPhase.IDLE: "",
}


@dataclass(frozen=True)
class RetryConfig:
    call_extra_attempts: int = DEFAULT_CALL_EXTRA_ATTEMPTS
    item_max_attempts: int = DEFAULT_ITEM_MAX_ATTEMPTS
    batch_rerun_enabled: bool = DEFAULT_BATCH_RERUN_ENABLED
    batch_rerun_max_rounds: int = DEFAULT_BATCH_RERUN_MAX_ROUNDS
    backoff_base_sec: float = DEFAULT_BACKOFF_BASE_SEC
    backoff_cap_sec: float = DEFAULT_BACKOFF_CAP_SEC

    @classmethod
    def from_settings(cls, settings: Optional[Dict] = None) -> "RetryConfig":
        s = settings or {}
        proc = s.get("processing") if isinstance(s, dict) else {}
        ar = (proc or {}).get("analysis_retry") if isinstance(proc, dict) else {}
        ar = ar if isinstance(ar, dict) else {}
        return cls(
            call_extra_attempts=int(
                ar.get("call_extra_attempts", DEFAULT_CALL_EXTRA_ATTEMPTS)
            ),
            item_max_attempts=int(
                ar.get("item_max_attempts", DEFAULT_ITEM_MAX_ATTEMPTS)
            ),
            batch_rerun_enabled=bool(
                ar.get("batch_rerun_enabled", DEFAULT_BATCH_RERUN_ENABLED)
            ),
            batch_rerun_max_rounds=min(
                int(ar.get("batch_rerun_max_rounds", DEFAULT_BATCH_RERUN_MAX_ROUNDS)),
                DEFAULT_BATCH_RERUN_MAX_ROUNDS,
            ),
            backoff_base_sec=float(
                ar.get("backoff_base_sec", DEFAULT_BACKOFF_BASE_SEC)
            ),
            backoff_cap_sec=float(ar.get("backoff_cap_sec", DEFAULT_BACKOFF_CAP_SEC)),
        )


def is_retriable(error: Any) -> bool:
    """T1：瞬时可重试 vs 不可恢复。

    接受 Exception、字符串、或带 status_code 的类 HTTP 错误对象。
    """
    if error is None:
        return False

    # 显式标记
    if isinstance(error, dict):
        if error.get("cancelled") or error.get("user_cancelled"):
            return False
        code = error.get("status_code") or error.get("code")
        if code is not None:
            try:
                c = int(code)
                if c in (401, 403):
                    return False
                if c in (429, 502, 503, 504):
                    return True
            except (TypeError, ValueError):
                pass
        msg = str(error.get("message") or error.get("error") or "")
    else:
        msg = str(error)
        code = getattr(error, "status_code", None)
        if code is None and hasattr(error, "response"):
            resp = getattr(error, "response", None)
            code = getattr(resp, "status_code", None) if resp is not None else None
        if code is not None:
            try:
                c = int(code)
                if c in (401, 403):
                    return False
                if c in (429, 502, 503, 504):
                    return True
            except (TypeError, ValueError):
                pass

    low = (msg or "").lower()
    # 不可恢复
    non_retriable_markers = (
        "401",
        "403",
        "unauthorized",
        "forbidden",
        "invalid api key",
        "authentication",
        "用户取消",
        "user cancelled",
        "user canceled",
        "cancelled by user",
        "文件不存在",
        "无法打开",
        "no such file",
        "not found",
        "file not found",
        "cannot open",
        "permission denied",  # 打开文件权限，偏不可恢复
    )
    for m in non_retriable_markers:
        if m in low:
            # 区分 HTTP 404 not found on API vs file — 若同时含 timeout 仍可重试
            if m in ("not found",) and ("http" in low or "api" in low or "404" in low):
                continue
            if "timeout" in low or "429" in low:
                continue
            return False

    retriable_markers = (
        "timeout",
        "timed out",
        "connection reset",
        "connection aborted",
        "connection error",
        "temporarily unavailable",
        "429",
        "502",
        "503",
        "504",
        "rate limit",
        "empty body",
        "empty response",
        "empty choices",
        "empty message",
        "empty transcription",
        "expecting value",  # json.JSONDecodeError 空 body
        "json decode",
        "jsondecodeerror",
        "failed to parse",
        "invalid json",
        "broken pipe",
        "remote end closed",
        "sslerror",  # 瞬时握手失败；证书配置类用不可重试路径
        "read timed out",
        "connect timeout",
    )
    for m in retriable_markers:
        if m in low:
            return True

    # JSONDecodeError 类型名
    name = type(error).__name__ if not isinstance(error, (str, dict)) else ""
    if name in ("JSONDecodeError", "TimeoutError", "APITimeoutError", "APIConnectionError"):
        return True

    return False


class AnalysisCancelled(Exception):
    """用户取消分析任务。"""


@dataclass
class ApiCallFailure:
    """调用层失败信息，供单条层决定是否 B 重试。"""
    message: str
    retriable: bool = True
    cancelled: bool = False


def normalize_progress_path(path: str) -> str:
    """进度/行临时态统一路径键。"""
    if not path:
        return ""
    try:
        from core.analysis_targets import path_status_key
        return path_status_key(path)
    except Exception:
        return str(path).replace("\\", "/").lower()


def should_retry_call(attempt_index: int, *, call_extra_attempts: int = DEFAULT_CALL_EXTRA_ATTEMPTS) -> bool:
    """attempt_index: 已完成的尝试次数（从 1 起算刚失败的那次）。

    共允许 (1 + call_extra_attempts) 次尝试；若刚失败的是第 k 次且 k <= 1+extra 且还能再试 → True
    例：extra=2 → 允许尝试 1,2,3；第 1 次失败后 should_retry_call(1)=True；第 3 次失败后 should_retry_call(3)=False
    """
    max_attempts = 1 + max(0, int(call_extra_attempts))
    return int(attempt_index) < max_attempts


def should_retry_item(attempt_b: int, *, item_max_attempts: int = DEFAULT_ITEM_MAX_ATTEMPTS) -> bool:
    """attempt_b: 已完成的完整单条尝试次数（1=初试已败）。

    最多 item_max_attempts 次完整尝试。
    """
    return int(attempt_b) < max(1, int(item_max_attempts))


def should_batch_rerun(
    failed_count: int,
    *,
    rerun_done: bool,
    enabled: bool = True,
    cancelled: bool = False,
    max_rounds: int = DEFAULT_BATCH_RERUN_MAX_ROUNDS,
) -> bool:
    if cancelled or not enabled:
        return False
    if rerun_done or int(max_rounds) < 1:
        return False
    return int(failed_count) > 0


def backoff_seconds(
    attempt_index: int,
    *,
    base: float = DEFAULT_BACKOFF_BASE_SEC,
    cap: float = DEFAULT_BACKOFF_CAP_SEC,
) -> float:
    """attempt_index 为即将进行的重试序号（1=第一次重试前等待）。"""
    if attempt_index <= 0:
        return 0.0
    delay = float(base) * (2 ** (int(attempt_index) - 1))
    return min(delay, float(cap))


# --- 进度事件与快照 ---


@dataclass
class ProgressSnapshot:
    overall_done: int = 0
    overall_total: int = 0
    sub_done: int = 0
    sub_total: int = 0
    round_kind: RoundKind = RoundKind.FIRST
    round_label: str = "首轮"
    message: str = ""
    rows: Dict[str, str] = field(default_factory=dict)  # path -> temp status label
    in_flight: int = 0
    cancelled: bool = False

    @property
    def overall_percent(self) -> int:
        if self.overall_total <= 0:
            return 0
        return min(100, int(100 * self.overall_done / self.overall_total))

    @property
    def sub_percent(self) -> int:
        if self.sub_total <= 0:
            return 0
        return min(100, int(100 * self.sub_done / self.sub_total))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "overall_done": self.overall_done,
            "overall_total": self.overall_total,
            "overall_percent": self.overall_percent,
            "sub_done": self.sub_done,
            "sub_total": self.sub_total,
            "sub_percent": self.sub_percent,
            "round_kind": self.round_kind.value,
            "round_label": self.round_label,
            "message": self.message,
            "rows": dict(self.rows),
            "in_flight": self.in_flight,
            "cancelled": self.cancelled,
        }


@dataclass
class ProgressReducer:
    """由事件归约进度快照。overall_total 在 JobStarted 时固定为分析目标集条数。"""

    overall_total: int = 0
    overall_done: int = 0  # 终态文件数（成功或最终失败），补跑成功也会增加
    # 已进入过终态的 path（首轮或补跑）
    terminal_ok: set = field(default_factory=set)
    terminal_fail: set = field(default_factory=set)
    sub_done: int = 0
    sub_total: int = 0
    round_kind: RoundKind = RoundKind.FIRST
    rows: Dict[str, str] = field(default_factory=dict)
    in_flight_paths: set = field(default_factory=set)
    cancelled: bool = False
    _phase_by_path: Dict[str, str] = field(default_factory=dict)

    def apply(self, event: str, **kwargs) -> ProgressSnapshot:
        if event == "job_started":
            self.overall_total = int(kwargs.get("target_count") or 0)
            self.overall_done = 0
            self.terminal_ok.clear()
            self.terminal_fail.clear()
            self.rows.clear()
            self.in_flight_paths.clear()
            self.cancelled = False
            self.sub_done = 0
            self.sub_total = self.overall_total
            self.round_kind = RoundKind.FIRST
        elif event == "round_started":
            kind = kwargs.get("kind") or RoundKind.FIRST
            if isinstance(kind, str):
                kind = RoundKind(kind)
            self.round_kind = kind
            self.sub_total = int(kwargs.get("item_count") or 0)
            self.sub_done = 0
        elif event == "item_phase":
            path = normalize_progress_path(kwargs.get("path") or "")
            phase = kwargs.get("phase") or AnalysisPhase.IDLE
            if isinstance(phase, str):
                try:
                    phase = AnalysisPhase(phase)
                except ValueError:
                    phase = AnalysisPhase.IDLE
            attempt_b = int(kwargs.get("attempt_b") or 1)
            pl = PHASE_LABELS.get(phase, "") if isinstance(phase, AnalysisPhase) else str(phase)
            if attempt_b > 1:
                label = f"重试中 {attempt_b}" + (f"·{pl}" if pl else "")
                self.rows[path] = label
            else:
                self.rows[path] = f"分析中·{pl}" if pl else "分析中"
            self._phase_by_path[path] = pl
            if path:
                self.in_flight_paths.add(path)
        elif event == "item_terminal":
            path = normalize_progress_path(kwargs.get("path") or "")
            ok = bool(kwargs.get("ok"))
            cancelled_item = bool(kwargs.get("cancelled"))
            if path in self.in_flight_paths:
                self.in_flight_paths.discard(path)
            if cancelled_item:
                self.rows[path] = "已取消"
                if path not in self.terminal_ok:
                    self.terminal_fail.add(path)
                    self.terminal_ok.discard(path)
            elif ok:
                self.rows[path] = "成功"
                if path in self.terminal_fail:
                    self.terminal_fail.discard(path)
                self.terminal_ok.add(path)
            else:
                self.rows[path] = "失败"
                if path not in self.terminal_ok:
                    self.terminal_fail.add(path)
            self.overall_done = len(self.terminal_ok | self.terminal_fail)
            self.sub_done = min(self.sub_total, self.sub_done + 1)
        elif event == "job_cancelled":
            self.cancelled = True
        return self.snapshot()

    def snapshot(self) -> ProgressSnapshot:
        if self.round_kind == RoundKind.RERUN:
            round_label = "补跑失败项"
            message = (
                f"补跑失败项 {self.sub_done}/{self.sub_total}"
                if self.sub_total
                else "补跑失败项"
            )
        else:
            round_label = "首轮"
            message = (
                f"首轮 {self.sub_done}/{self.sub_total}"
                if self.sub_total
                else "分析任务"
            )
        if self.in_flight_paths:
            message += f" · 进行中 {len(self.in_flight_paths)}"
        if self.cancelled:
            message += " · 已取消"
        return ProgressSnapshot(
            overall_done=self.overall_done,
            overall_total=self.overall_total,
            sub_done=self.sub_done,
            sub_total=self.sub_total,
            round_kind=self.round_kind,
            round_label=round_label,
            message=message,
            rows=dict(self.rows),
            in_flight=len(self.in_flight_paths),
            cancelled=self.cancelled,
        )


def row_temp_label(status: Union[str, RowTempStatus], phase: str = "") -> str:
    if isinstance(status, RowTempStatus):
        status = status.value
    if status == RowTempStatus.RETRYING.value:
        return f"重试中·{phase}" if phase else "重试中"
    if status == RowTempStatus.ANALYZING.value:
        return f"分析中·{phase}" if phase else "分析中"
    if status == RowTempStatus.SUCCESS.value:
        return "成功"
    if status == RowTempStatus.FAILED.value:
        return "失败"
    if status == RowTempStatus.CANCELLED.value:
        return "已取消"
    return str(status)


def sleep_backoff(
    attempt_index: int,
    *,
    config: Optional[RetryConfig] = None,
    sleeper: Optional[Callable[[float], None]] = None,
) -> float:
    """执行退避等待；sleeper 可注入（测试用 lambda _: None）。返回实际等待秒数。"""
    cfg = config or RetryConfig()
    delay = backoff_seconds(
        attempt_index, base=cfg.backoff_base_sec, cap=cfg.backoff_cap_sec
    )
    if delay > 0 and sleeper is not None:
        sleeper(delay)
    elif delay > 0:
        import time

        time.sleep(delay)
    return delay


def default_analysis_retry_settings_dict() -> Dict[str, Any]:
    return {
        "call_extra_attempts": DEFAULT_CALL_EXTRA_ATTEMPTS,
        "item_max_attempts": DEFAULT_ITEM_MAX_ATTEMPTS,
        "batch_rerun_enabled": DEFAULT_BATCH_RERUN_ENABLED,
        "batch_rerun_max_rounds": DEFAULT_BATCH_RERUN_MAX_ROUNDS,
    }