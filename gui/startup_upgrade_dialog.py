# -*- coding: utf-8 -*-
"""首次启动升级的确认、进度和服务层门禁。"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QProgressBar,
    QVBoxLayout,
)

from core.startup_upgrade import UpgradeResult, UpgradeStatus
from core.video_organizer_service import VideoOrganizerService


class StartupUpgradeWorker(QThread):
    """在 Qt 线程中执行升级预检或导入，避免阻塞窗口事件循环。"""

    progress = Signal(int, int)
    result_ready = Signal(object)

    def __init__(self, *, database_path: str, json_path: str, execute: bool):
        super().__init__()
        self.database_path = database_path
        self.json_path = json_path
        self.execute_upgrade = execute

    def run(self) -> None:
        try:
            if self.execute_upgrade:
                result = VideoOrganizerService.run_startup_upgrade(
                    confirmed=True,
                    db_path=self.database_path,
                    results_json=self.json_path,
                    on_progress=self._emit_progress,
                )
            else:
                result = VideoOrganizerService.inspect_startup_upgrade(
                    db_path=self.database_path,
                    results_json=self.json_path,
                    include_import_report=True,
                )
        except Exception as exc:  # 服务层应返回失败结果；此处保护 GUI 线程
            result = UpgradeResult(
                status=UpgradeStatus.FAILED,
                database_path=self.database_path,
                json_path=self.json_path,
                message=f"启动升级失败: {exc}",
                error=str(exc),
            )
        self.result_ready.emit(result)

    def _emit_progress(self, current: int, total: int) -> None:
        self.progress.emit(current, total)


class StartupUpgradeDialog(QDialog):
    """首次旧结果导入的确认门禁和进度反馈。"""

    def __init__(self, pending_result: UpgradeResult, parent=None):
        super().__init__(parent)
        self.pending_result = pending_result
        self.final_result: Optional[UpgradeResult] = None
        self._worker: Optional[StartupUpgradeWorker] = None

        self.setWindowTitle("首次升级确认")
        self.setModal(True)
        self.setWindowModality(Qt.ApplicationModal)
        self.resize(520, 260)

        layout = QVBoxLayout(self)
        self.title_label = QLabel("检测到旧版视频分析结果")
        self.title_label.setStyleSheet("font-weight: bold; font-size: 15px;")
        layout.addWidget(self.title_label)

        self.summary_label = QLabel("正在读取升级预检报告，请稍候……")
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setVisible(True)
        layout.addWidget(self.progress_bar)

        self.buttons = QDialogButtonBox()
        self.confirm_button = self.buttons.addButton(
            "确认并导入", QDialogButtonBox.AcceptRole
        )
        self.cancel_button = self.buttons.addButton(
            "取消启动", QDialogButtonBox.RejectRole
        )
        self.confirm_button.setEnabled(False)
        self.cancel_button.setEnabled(False)
        self.buttons.accepted.connect(self._confirm)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        self._start_worker(execute=False)

    def _start_worker(self, *, execute: bool) -> None:
        self._worker = StartupUpgradeWorker(
            database_path=self.pending_result.database_path,
            json_path=self.pending_result.json_path,
            execute=execute,
        )
        self._worker.result_ready.connect(
            self._on_result if not execute else self._on_execution_result
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.start()

    def _on_worker_finished(self) -> None:
        worker = self.sender()
        if worker is not self._worker:
            return
        worker.deleteLater()

    def _on_result(self, result: UpgradeResult) -> None:
        self.pending_result = result
        if result.status is UpgradeStatus.NEEDS_CONFIRMATION:
            report = result.report
            skipped = report.get("skipped_rows", 0)
            detail = (
                f"共 {report.get('total_rows', 0)} 条，"
                f"可导入 {report.get('valid_rows', 0)} 条，"
                f"将跳过 {skipped} 条。"
            )
            if report.get("report_truncated"):
                detail += " 详细问题报告已按上限截取。"
            self.summary_label.setText(
                f"{detail}\n确认后将创建管理数据库并分批导入；原 JSON 文件会保留。"
            )
            self.progress_bar.setVisible(False)
            self.confirm_button.setEnabled(True)
            self.cancel_button.setEnabled(True)
            return
        if result.status is UpgradeStatus.NO_MIGRATION:
            self.final_result = result
            self.accept()
            return
        self.cancel_button.setEnabled(True)
        self._show_failure(result)

    def _confirm(self) -> None:
        self.confirm_button.setEnabled(False)
        self.cancel_button.setEnabled(False)
        self.summary_label.setText("正在备份、导入并验证管理数据，窗口仍可响应……")
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)
        self._start_worker(execute=True)

    def _on_progress(self, current: int, total: int) -> None:
        if total <= 0:
            self.progress_bar.setRange(0, 0)
            return
        self.progress_bar.setRange(0, total)
        self.progress_bar.setValue(current)
        self.progress_bar.setFormat(f"已导入 {current}/{total}")

    def _on_execution_result(self, result: UpgradeResult) -> None:
        self.final_result = result
        if result.status is UpgradeStatus.COMPLETED:
            self.summary_label.setText("首次升级已完成，正在打开应用……")
            self.accept()
            return
        self.cancel_button.setEnabled(True)
        self.confirm_button.setEnabled(True)
        self.confirm_button.setText("重试")
        self._show_failure(result, allow_retry=True)

    def _show_failure(self, result: UpgradeResult, *, allow_retry: bool = False) -> None:
        self.progress_bar.setVisible(False)
        self.summary_label.setText(
            f"启动升级无法继续：{result.error or result.message}\n"
            "原始数据未被删除；可以取消启动后检查文件，或重试。"
        )
        self.confirm_button.setEnabled(
            allow_retry or bool(result.report.get("incomplete_upgrade"))
        )

    def closeEvent(self, event) -> None:
        if self._worker is not None and self._worker.isRunning():
            event.ignore()
            return
        super().closeEvent(event)


__all__ = ["StartupUpgradeDialog", "StartupUpgradeWorker"]
