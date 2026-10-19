# -*- coding: utf-8 -*-
import sys

from PySide6.QtCore import QThread, Qt
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QMessageBox,
    QProgressDialog,
)

from core.data_directory import (
    DataDirectoryError,
    DataDirectoryManager,
    DataDirectoryState,
)
from core.startup_upgrade import UpgradeStatus
from core.video_organizer_service import VideoOrganizerService
from gui.main_window import MainWindow
from gui.startup_upgrade_dialog import StartupUpgradeDialog


class _DataDirectoryWorker(QThread):
    def __init__(self, operation):
        super().__init__()
        self.operation = operation
        self.error = ""

    def run(self) -> None:
        try:
            self.operation()
        except Exception as exc:
            self.error = str(exc) or exc.__class__.__name__


def _run_data_directory_operation(operation, message: str) -> bool:
    progress = QProgressDialog(message, "", 0, 0)
    progress.setWindowTitle("管理数据目录")
    progress.setWindowModality(Qt.ApplicationModal)
    progress.setCancelButton(None)
    progress.setMinimumDuration(0)
    progress.setWindowFlag(Qt.WindowCloseButtonHint, False)

    worker = _DataDirectoryWorker(operation)
    worker.finished.connect(progress.accept)
    worker.start()
    progress.exec()
    worker.wait()
    if worker.error:
        QMessageBox.critical(None, "数据目录操作失败", worker.error)
        return False
    return True


def _run_legacy_data_migration(
    manager: DataDirectoryManager,
    target_root: str,
) -> bool:
    return _run_data_directory_operation(
        lambda: manager.migrate_legacy(target_root),
        "正在备份、复制并验证旧管理数据…",
    )


def _choose_alternate_data_directory(manager: DataDirectoryManager) -> bool:
    target = QFileDialog.getExistingDirectory(None, "选择可写的管理数据目录")
    if not target:
        return False
    try:
        if manager.has_management_data(target):
            answer = QMessageBox.question(
                None,
                "打开已有数据目录",
                "目标目录已有管理数据。是否打开该目录？\n不会与当前目录合并。",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return False
            return _run_data_directory_operation(
                lambda: manager.open_existing(target),
                "正在校验并打开已有管理数据目录…",
            )
        return _run_data_directory_operation(
            lambda: manager.activate_empty(target),
            "正在准备新的管理数据目录…",
        )
    except DataDirectoryError as exc:
        QMessageBox.critical(None, "数据目录不可用", str(exc))
        return False


def _prepare_data_directory() -> bool:
    """启动前处理旧布局和默认目录权限；不静默丢弃或覆盖旧管理数据。"""
    manager = DataDirectoryManager()
    try:
        inspection = manager.inspect()
        if inspection.state is DataDirectoryState.LEGACY:
            default_has_data = manager.has_management_data(inspection.default_root)
            if default_has_data:
                answer = QMessageBox.question(
                    None,
                    "选择管理数据来源",
                    "程序旁仍有旧管理数据，但默认 data 目录也已有管理数据。\n"
                    "选择“是”打开已有 data 目录；选择“否”另选空目录迁移旧数据；取消则退出。",
                    QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
                    QMessageBox.Cancel,
                )
                if answer == QMessageBox.Cancel:
                    return False
                if answer == QMessageBox.Yes:
                    return _run_data_directory_operation(
                        lambda: manager.open_existing(inspection.default_root),
                        "正在校验并打开已有管理数据目录…",
                    )
                target = QFileDialog.getExistingDirectory(
                    None,
                    "选择旧数据迁移目标目录",
                )
                if not target:
                    return False
                return _run_legacy_data_migration(manager, target)

            answer = QMessageBox.question(
                None,
                "迁移管理数据",
                "检测到程序旁旧管理数据。是否复制到程序旁 data 目录并使用新目录？\n"
                + "来源: " + inspection.application_directory + "\n"
                + "目标: " + inspection.default_root + "\n"
                + "原目录会保留，不会移动原始视频。",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if answer != QMessageBox.Yes:
                return False
            try:
                manager.ensure_writable(inspection.default_root, create=True)
            except DataDirectoryError as exc:
                QMessageBox.warning(
                    None,
                    "无法迁移到默认目录",
                    str(exc) + "\n请选择一个空的可写目录完成迁移。",
                )
                target = QFileDialog.getExistingDirectory(
                    None,
                    "选择旧数据迁移目标目录",
                )
                if not target:
                    return False
                return _run_legacy_data_migration(manager, target)
            return _run_legacy_data_migration(manager, inspection.default_root)
        elif inspection.state is DataDirectoryState.INVALID:
            QMessageBox.warning(
                None,
                "数据目录不可用",
                inspection.message + "\n请选择其他可写的管理数据目录。",
            )
            return _choose_alternate_data_directory(manager)
        elif inspection.state is DataDirectoryState.DEFAULT_NEW:
            try:
                manager.ensure_writable(inspection.default_root, create=True)
            except DataDirectoryError as exc:
                QMessageBox.warning(
                    None,
                    "默认数据目录不可写",
                    str(exc) + "\n请选择其他可写的管理数据目录。",
                )
                return _choose_alternate_data_directory(manager)
        return True
    except DataDirectoryError as exc:
        QMessageBox.critical(None, "数据目录迁移失败", str(exc))
        return False


def _prepare_startup_upgrade(app: QApplication) -> bool:
    """在创建主窗口前完成旧结果升级确认；取消时不打开半成品服务。"""
    try:
        pending = VideoOrganizerService.inspect_startup_upgrade(
            include_import_report=False
        )
    except Exception as exc:
        QMessageBox.critical(None, "启动检查失败", str(exc))
        return False

    needs_dialog = pending.status is UpgradeStatus.NEEDS_CONFIRMATION or (
        pending.status is UpgradeStatus.FAILED
        and pending.report.get("incomplete_upgrade")
    )
    if needs_dialog:
        dialog = StartupUpgradeDialog(pending)
        if dialog.exec() != QDialog.Accepted:
            return False
        result = dialog.final_result
        if result is not None and not result.ok:
            QMessageBox.critical(None, "启动升级失败", result.error or result.message)
            return False
        return True

    if pending.status is UpgradeStatus.FAILED:
        QMessageBox.critical(None, "数据库启动检查失败", pending.error or pending.message)
        return False
    return True


def main():
    app = QApplication(sys.argv)

    if not _prepare_data_directory():
        return 1

    if not _prepare_startup_upgrade(app):
        return 1

    # 初始化服务层；此时旧 JSON 若存在，已经完成确认、导入、验证和记录。
    try:
        service = VideoOrganizerService()
    except Exception as exc:
        QMessageBox.critical(None, "应用启动失败", str(exc))
        return 1

    # 启动主窗口
    window = MainWindow(service)
    window.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
