# -*- coding: utf-8 -*-
from PySide6.QtCore import QThread, Signal, Qt
from core.video_organizer_service import VideoOrganizerService
import time


def analysis_task_succeeded(result) -> bool:
    """
    根据 run_analysis 返回字典判定任务级成功标志。
    - 无可分析项：成功结束（空跑）
    - 有尝试且全部成功：True
    - 全部失败或部分失败：False
    - 用户取消且有失败：False
    """
    if not isinstance(result, dict):
        return True
    attempted = int(result.get("attempted") or 0)
    succeeded = int(result.get("succeeded") or 0)
    failed = int(result.get("failed") or 0)
    if attempted <= 0:
        return True
    if result.get("cancelled") and failed > 0:
        return False
    if succeeded <= 0:
        return False
    if failed > 0:
        return False
    return True


class AnalysisWorker(QThread):
    """异步分析 Worker（进度/日志向 GUI 节流，避免大批量卡死主线程）。"""
    progress_updated = Signal(int, str)  # 兼容：总进度百分比 + 文案
    snapshot_updated = Signal(dict)  # 结构化进度快照
    task_finished = Signal(bool, str)

    def __init__(self, service: VideoOrganizerService, input_path, force_reanalyze: bool = False):
        super().__init__()
        self.service = service
        self.input_path = input_path
        self.force_reanalyze = force_reanalyze
        self._cancel_requested = False
        self._last_log_emit = 0.0
        self._last_snapshot_emit = 0.0
        self._log_interval = 0.3
        self._snapshot_interval = 0.3
        self._pending_log = ""
        self._pending_snap = None

    def request_cancel(self):
        self._cancel_requested = True
        try:
            self.service.request_cancel_analysis()
        except Exception:
            pass

    def handle_log(self, msg):
        # 节流：高频「正在处理」日志合并，防止 QueuedConnection 淹没 GUI 事件循环
        now = time.monotonic()
        self._pending_log = msg or ""
        if (now - self._last_log_emit) < self._log_interval:
            return
        self._last_log_emit = now
        self.progress_updated.emit(-1, self._pending_log)

    def handle_progress(self, current, total):
        # 有结构化 snapshot 时由 handle_snapshot 驱动 UI，避免双通道抖动
        if getattr(self, "_saw_snapshot", False):
            return
        if total <= 0:
            self.progress_updated.emit(0, "没有待分析视频")
            return
        progress = int((current / total) * 100)
        self.progress_updated.emit(progress, f"总进度: {current}/{total}")

    def handle_snapshot(self, snap: dict):
        if not isinstance(snap, dict):
            return
        self._saw_snapshot = True
        # 永远不向 GUI 传递上千行临时态
        snap = dict(snap)
        snap["rows"] = {}
        now = time.monotonic()
        self._pending_snap = snap
        force = bool(snap.get("cancelled")) or (
            int(snap.get("overall_done") or 0) >= int(snap.get("overall_total") or 0)
            and int(snap.get("overall_total") or 0) > 0
        )
        if not force and (now - self._last_snapshot_emit) < self._snapshot_interval:
            return
        self._last_snapshot_emit = now
        self.snapshot_updated.emit(snap)
        op = int(snap.get("overall_percent") or 0)
        msg = snap.get("message") or ""
        self.progress_updated.emit(op, msg)

    def run(self):
        try:
            self._saw_snapshot = False
            self.service.on_log = self.handle_log
            self.service.on_progress = self.handle_progress
            self.service.on_analysis_snapshot = self.handle_snapshot

            result = self.service.run_analysis(
                self.input_path, force_reanalyze=self.force_reanalyze
            )
            # 冲刷节流中的最后一条日志/快照
            if self._pending_log:
                self.progress_updated.emit(-1, self._pending_log)
            if self._pending_snap:
                self.snapshot_updated.emit(self._pending_snap)
            if isinstance(result, dict):
                msg = result.get("message") or "分析任务已完成"
                ok = analysis_task_succeeded(result)
                self.task_finished.emit(ok, msg)
            else:
                self.task_finished.emit(True, "分析任务已完成")
        except Exception as e:
            self.task_finished.emit(False, f"分析出错: {str(e)}")