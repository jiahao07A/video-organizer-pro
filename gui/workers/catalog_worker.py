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
) -> CatalogQueryWorker:
    """Start a query and keep the worker alive on the view."""
    existing = getattr(parent, "_catalog_worker", None)
    if existing is not None and existing.isRunning():
        existing.requestInterruption()

    worker = CatalogQueryWorker(fn, parent)
    parent._catalog_worker = worker

    def finish_ok(payload):
        try:
            on_ok(payload)
        finally:
            worker.deleteLater()
            if getattr(parent, "_catalog_worker", None) is worker:
                parent._catalog_worker = None

    def finish_error(message):
        try:
            if on_fail:
                on_fail(message)
        finally:
            worker.deleteLater()
            if getattr(parent, "_catalog_worker", None) is worker:
                parent._catalog_worker = None

    worker.completed.connect(finish_ok)
    worker.failed.connect(finish_error)
    worker.start()
    return worker
