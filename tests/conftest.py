"""测试进程的Qt入口；不加载用户配置或创建业务服务。"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest


@pytest.fixture(scope="session")
def app():
    from PySide6.QtWidgets import QApplication
    application = QApplication.instance() or QApplication([])
    # 会话持有强引用，避免单模块结束后QApplication被回收。
    yield application


@pytest.fixture(scope="session")
def qt_app(app):
    return app
