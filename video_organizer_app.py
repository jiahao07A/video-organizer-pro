# -*- coding: utf-8 -*-
import sys
from PySide6.QtWidgets import QApplication
from qt_material import apply_stylesheet
from core.video_organizer_service import VideoOrganizerService
from gui.main_window import MainWindow


def main():
    app = QApplication(sys.argv)

    # 初始化服务层
    service = VideoOrganizerService()

    # 启动主窗口
    window = MainWindow(service)
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
