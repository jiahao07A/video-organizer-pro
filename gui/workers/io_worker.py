# -*- coding: utf-8 -*-
"""通用后台 IO/CPU 任务 Worker（扫盘、导出、XMP、迁移、故事板等）。"""
from __future__ import annotations

from typing import Any, Callable, Optional

from PySide6.QtCore import QThread, Signal


class IoWorker(QThread):
    """在后台线程执行无 Qt UI 副作用的可调用对象。"""

    progress_updated = Signal(int, str)  # percent or -1, message
    task_finished = Signal(bool, object)  # ok, result_or_error_str

    def __init__(self, fn: Callable[[], Any], parent=None):
        super().__init__(parent)
        self._fn = fn

    def run(self) -> None:
        try:
            result = self._fn()
            self.task_finished.emit(True, result)
        except Exception as e:
            self.task_finished.emit(False, str(e) or e.__class__.__name__)


def run_io_job(
    parent,
    *,
    fn: Callable[[], Any],
    on_ok: Callable[[Any], None],
    on_fail: Optional[Callable[[str], None]] = None,
    on_start: Optional[Callable[[], None]] = None,
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

    def _ok(result):
        try:
            on_ok(result)
        finally:
            worker.deleteLater()
            if getattr(parent, "_io_worker", None) is worker:
                parent._io_worker = None

    def _err(msg):
        try:
            if on_fail:
                on_fail(str(msg) if not isinstance(msg, str) else msg)
            else:
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.warning(parent, "任务失败", str(msg) or "未知错误")
        finally:
            worker.deleteLater()
            if getattr(parent, "_io_worker", None) is worker:
                parent._io_worker = None

    def _finished(ok, payload):
        if ok:
            _ok(payload)
        else:
            _err(payload)

    worker.task_finished.connect(_finished)
    if hasattr(parent, "status_label") and busy_message:
        try:
            parent.status_label.setText(busy_message)
        except Exception:
            pass
    worker.start()
    return worker