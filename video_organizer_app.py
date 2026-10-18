# -*- coding: utf-8 -*-
import sys

from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from core.data_directory import (
    DataDirectoryError,
    DataDirectoryManager,
    DataDirectoryState,
)
from core.startup_upgrade import UpgradeStatus
from core.video_organizer_service import VideoOrganizerService
from gui.main_window import MainWindow
from gui.startup_upgrade_dialog import StartupUpgradeDialog


def _prepare_data_directory() -> bool:
    """启动前处理程序旁旧布局；不静默丢弃或覆盖旧管理数据。"""
    manager = DataDirectoryManager()
    try:
        inspection = manager.inspect()
        if inspection.state is DataDirectoryState.LEGACY:
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
            manager.migrate_legacy()
        elif inspection.state is DataDirectoryState.INVALID:
            QMessageBox.critical(None, "数据目录不可用", inspection.message)
            return False
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
