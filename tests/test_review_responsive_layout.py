"""96 DPI 离屏真实窗口几何回归；不替代桌面缩放实测。"""
import time

import pytest
from PySide6.QtCore import QPoint, QRect, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QAbstractButton, QPushButton, QWidget

from core.video_organizer_service import SettingsManager, VideoOrganizerService
from gui.main_window import MainWindow
from gui.services.settings_controller import SettingsController
from gui.views.settings import _window_has_active_background_work
from gui.widgets.flow_layout import FlowLayout


@pytest.fixture
def window(qt_app, tmp_path, monkeypatch):
    monkeypatch.setattr("core.video_organizer_service.TAG_CONFIG_FILE", str(tmp_path / "tags.json"))
    monkeypatch.setattr("core.video_organizer_service.get_settings_file_path", lambda: str(tmp_path / "settings.json"))
    monkeypatch.setattr("gui.services.update_controller.UpdateController.start_automatic_checks", lambda _: None)
    service = VideoOrganizerService(settings=SettingsManager.deep_copy_defaults(), on_log=lambda _: None,
        db_path=str(tmp_path / "catalog.db"), results_json=str(tmp_path / "results.json"),
        results_csv=str(tmp_path / "results.csv"))
    view = MainWindow(service)
    yield view
    for controller in view.findChildren(SettingsController):
        controller.cancel()
    # 标签页构造后500ms安排首次统计；几何测试等待该已排队任务再清理页面。
    QTest.qWait(550)
    for timer in view.findChildren(QTimer):
        timer.stop()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        qt_app.processEvents()
        QTest.qWait(10)
        qt_app.processEvents()
        if not _window_has_active_background_work(view):
            break
    assert not _window_has_active_background_work(view)
    view._allow_exit = True
    view.close()
    view.deleteLater()
    qt_app.processEvents()


def _assert_flow_geometry(page):
    flows = page.findChildren(FlowLayout)
    assert flows, "实际工具栏必须使用可换行布局"
    for flow in flows:
        visible = [flow.itemAt(i).widget() for i in range(flow.count())
                   if flow.itemAt(i).widget() is not None and not flow.itemAt(i).isEmpty()]
        rectangles = [widget.geometry() for widget in visible]
        if not rectangles:
            continue
        for rectangle in rectangles:
            assert flow.geometry().contains(rectangle), (flow.geometry(), rectangle)
        for index, rectangle in enumerate(rectangles):
            assert all(not rectangle.intersects(other) for other in rectangles[index + 1:])


@pytest.mark.parametrize("width,height", [(1280, 800), (1554, 930)])
def test_window_pages_fit_with_reachable_settings_save(window, qt_app, width, height):
    window.resize(width, height)
    window.show()
    qt_app.processEvents()
    assert window.width() <= width
    assert window.height() <= height
    for index in range(5):
        page = window._ensure_page(index)
        window.content_stack.setCurrentWidget(page)
        qt_app.processEvents()
        assert window.minimumSizeHint().width() <= width
        assert window.minimumSizeHint().height() <= height
        assert window.width() <= width and window.height() <= height
        if index in (0, 1):
            _assert_flow_geometry(page)
            if width == 1280:
                toolbar = page.layout().itemAt(1).layout()
                assert isinstance(toolbar, FlowLayout)
                assert len({toolbar.itemAt(i).widget().y() for i in range(toolbar.count())
                            if not toolbar.itemAt(i).isEmpty()}) > 1
            panel = page.detail_panel
            for button in panel.findChildren(QAbstractButton):
                if button.isVisible():
                    mapped = QRect(button.mapTo(panel, QPoint(0, 0)), button.size())
                    assert panel.rect().contains(mapped), (button.text(), panel.rect(), mapped)
        if index == 3:
            button = page.save_btn
            mapped = QRect(button.mapTo(window, QPoint(0, 0)), button.size())
            assert button.isVisible() and window.rect().contains(mapped)


def test_flow_hidden_items_do_not_reserve_space_and_margins_are_respected(qt_app):
    container = QWidget()
    flow = FlowLayout(container, margin=4, spacing=5)
    first, hidden, last = [QPushButton(text) for text in ("first", "hidden", "last")]
    for button in (first, hidden, last):
        button.setFixedSize(70, 24)
        flow.addWidget(button)
    hidden.hide()
    container.resize(160, 100)
    container.show()
    qt_app.processEvents()
    try:
        assert flow.heightForWidth(160) == 32
        assert first.y() == last.y() == 4
        assert first.x() == 4 and last.x() == 79
        assert flow.minimumSize().width() == 78
    finally:
        container.close()
