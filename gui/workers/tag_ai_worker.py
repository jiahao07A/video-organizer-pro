# -*- coding: utf-8 -*-
"""标签库 AI 后台任务：避免在 UI 线程同步打模型导致界面卡死。"""
from __future__ import annotations

from typing import Any, Callable, Optional

from PySide6.QtCore import QThread, Signal


class TagAiWorker(QThread):
    """在后台线程执行任意无 Qt UI 副作用的可调用对象。"""

    finished_ok = Signal(object)  # 成功结果（任意可跨线程传递的对象）
    failed = Signal(str)  # 错误信息

    def __init__(self, fn: Callable[[], Any], parent=None):
        super().__init__(parent)
        self._fn = fn

    def run(self) -> None:
        try:
            result = self._fn()
            self.finished_ok.emit(result)
        except Exception as e:
            self.failed.emit(str(e) or e.__class__.__name__)


def run_tag_ai_job(
    parent,
    *,
    title: str,
    label: str,
    fn: Callable[[], Any],
    on_ok: Callable[[Any], None],
    on_fail: Optional[Callable[[str], None]] = None,
    busy_widgets=None,
) -> TagAiWorker:
    """
    启动后台 AI 任务并显示不可取消的忙碌对话框（模型调用中途难可靠取消）。
    parent 上挂 _tag_ai_worker 引用，防止被 GC。
    若已有任务在跑，直接调用 on_fail。
    """
    from PySide6.QtWidgets import QProgressDialog, QMessageBox
    from PySide6.QtCore import Qt

    existing = getattr(parent, "_tag_ai_worker", None)
    if existing is not None and existing.isRunning():
        msg = "已有标签库 AI 任务在运行，请稍候。"
        if on_fail:
            on_fail(msg)
        else:
            QMessageBox.information(parent, "请稍候", msg)
        return existing

    progress = QProgressDialog(label, None, 0, 0, parent)
    progress.setWindowTitle(title)
    progress.setWindowModality(Qt.WindowModal)
    progress.setMinimumDuration(0)
    progress.setCancelButton(None)
    progress.setRange(0, 0)  # busy
    progress.show()

    widgets = list(busy_widgets or [])
    for w in widgets:
        try:
            w.setEnabled(False)
        except Exception:
            pass

    worker = TagAiWorker(fn, parent)
    parent._tag_ai_worker = worker

    def _cleanup():
        try:
            progress.close()
        except Exception:
            pass
        for w in widgets:
            try:
                w.setEnabled(True)
            except Exception:
                pass

    def _ok(result):
        _cleanup()
        try:
            on_ok(result)
        finally:
            worker.deleteLater()
            if getattr(parent, "_tag_ai_worker", None) is worker:
                parent._tag_ai_worker = None

    def _err(msg: str):
        _cleanup()
        try:
            if on_fail:
                on_fail(msg)
            else:
                QMessageBox.warning(parent, "AI 失败", msg or "未知错误")
        finally:
            worker.deleteLater()
            if getattr(parent, "_tag_ai_worker", None) is worker:
                parent._tag_ai_worker = None

    worker.finished_ok.connect(_ok)
    worker.failed.connect(_err)
    worker.start()
    return worker