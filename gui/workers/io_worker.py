# -*- coding: utf-8 -*-
"""通用后台 IO/CPU 任务 Worker（扫盘、导出、XMP、迁移、故事板等）。"""
from __future__ import annotations

from typing import Any, Callable, Optional

from core.diagnostic_logging import bind_worker, worker_run, worker_exception
from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot


class _ProgressRelay(QObject):
    """在创建任务的线程交付快照，后台任务不直接接触界面。"""

    def __init__(self, callback, parent):
        super().__init__(parent)
        self._callback = callback

    @Slot(object)
    def deliver(self, snapshot):
        self._callback(snapshot)


class IoWorker(QThread):
    """在后台线程执行无 Qt UI 副作用的可调用对象。"""

    progress_updated = Signal(int, str)  # percent or -1, message
    progress_snapshot = Signal(object)  # optional structured task progress
    task_finished = Signal(bool, object)  # ok, result_or_error_str

    def __init__(self, fn: Callable[[], Any], parent=None):
        super().__init__(parent)
        bind_worker(self)
        self._fn = fn

    @worker_run
    def run(self) -> None:
        try:
            result = self._fn()
            self.task_finished.emit(True, result)
        except Exception as e:
            worker_exception()
            self.task_finished.emit(False, str(e) or e.__class__.__name__)


def run_io_job(
    parent,
    *,
    fn: Callable[[], Any],
    on_ok: Callable[[Any], None],
    on_fail: Optional[Callable[[str], None]] = None,
    on_start: Optional[Callable[[], None]] = None,
    on_progress: Optional[Callable[[Any], None]] = None,
    busy_message: str = "处理中…",
) -> IoWorker:
    """
    启动后台任务；parent 上挂 _io_worker 防 GC。
    若已有任务在跑，调用 on_fail。
    """
    existing = getattr(parent, "_io_worker", None)
    if existing is not None and existing.isRunning():
        msg = "已有后台任务在运行，请稍候。"
        if on_fail:
            on_fail(msg)
        return existing

    if on_start:
        on_start()

    worker = IoWorker(fn, parent)
    parent._io_worker = worker
    if on_progress is not None:
        relay = _ProgressRelay(on_progress, worker)
        worker.progress_snapshot.connect(relay.deliver, Qt.QueuedConnection)

    def _ok(result):
        on_ok(result)

    def _err(msg):
        if on_fail:
            on_fail(str(msg) if not isinstance(msg, str) else msg)
        else:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(parent, "任务失败", str(msg) or "未知错误")

    outcome = []
    def _finished(ok, payload):
        outcome[:] = [ok, payload]

    worker.task_finished.connect(_finished)
    def cleanup():
        if getattr(parent, '_io_worker', None) is worker:
            parent._io_worker = None
        worker.deleteLater()
        if outcome:
            if outcome[0]:
                _ok(outcome[1])
            else:
                _err(outcome[1])
    worker.finished.connect(cleanup)
    if hasattr(parent, "status_label") and busy_message:
        try:
            parent.status_label.setText(busy_message)
        except Exception:
            worker_exception()
            pass
    worker.start()
    return worker
