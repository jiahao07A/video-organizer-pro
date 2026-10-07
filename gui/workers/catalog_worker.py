# -*- coding: utf-8 -*-
"""Background workers for database-backed catalog page queries."""
from __future__ import annotations

from typing import Any, Callable, Optional

from PySide6.QtCore import QThread, Signal


class CatalogQueryWorker(QThread):
    """Run one catalog query away from the Qt GUI thread."""

    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, fn: Callable[[], Any], parent=None):
        super().__init__(parent)
        self._fn = fn

    def run(self) -> None:
        try:
            self.completed.emit(self._fn())
        except Exception as exc:
            self.failed.emit(str(exc) or exc.__class__.__name__)


def run_catalog_query(
    parent,
    *,
    fn: Callable[[], Any],
    on_ok: Callable[[Any], None],
    on_fail: Optional[Callable[[str], None]] = None,
    worker_key: str = "_catalog_worker",
) -> CatalogQueryWorker:
    """启动查询；分页与详情可使用独立 worker_key，避免互相覆盖生命周期。"""
    existing = getattr(parent, worker_key, None)
    if existing is not None and existing.isRunning():
        existing.requestInterruption()

    worker = CatalogQueryWorker(fn, parent)
    setattr(parent, worker_key, worker)

    def finish_error(message):
        if on_fail:
            on_fail(message)

    def cleanup():
        if getattr(parent, worker_key, None) is worker:
            setattr(parent, worker_key, None)
        worker.deleteLater()

    # completed 早于线程退出；等 finished 再销毁 QThread。
    worker.completed.connect(on_ok)
    worker.failed.connect(finish_error)
    worker.finished.connect(cleanup)
    worker.start()
    return worker
