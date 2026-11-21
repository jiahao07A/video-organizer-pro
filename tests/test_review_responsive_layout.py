"""96 DPI 离屏真实窗口几何回归；不替代桌面缩放实测。"""
import time

import pytest
from PySide6.QtCore import QPoint, QRect, QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QAbstractButton, QPushButton, QScrollArea, QWidget

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


@pytest.mark.parametrize("width,height", [(1280, 780), (1554, 930)])
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
            action_buttons = (panel.save_btn, panel.ai_rec_btn, panel.storyboard_btn, panel.export_tags_btn)
            for button in action_buttons:
                required_width = max(button.minimumSizeHint().width(), button.sizeHint().width())
                assert button.width() >= required_width, (button.text(), button.width(), required_width)
            rectangles = [button.geometry() for button in action_buttons]
            for position, rectangle in enumerate(rectangles):
                assert all(not rectangle.intersects(other) for other in rectangles[position + 1:])
            for button in panel.findChildren(QAbstractButton):
                if button.isVisible():
                    _reachable_point(window, button, qt_app)
        if index == 3:
            button = page.reset_btn
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


def _wait_for(qt_app, condition, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qt_app.processEvents()
        if condition():
            return
        time.sleep(.02)
    assert condition(), "界面或媒体未在期限内就绪"


def _reachable_point(window, widget, qt_app):
    parent = widget.parentWidget()
    while parent is not None:
        if isinstance(parent, QScrollArea):
            center = widget.mapTo(parent.widget(), widget.rect().center())
            # QTextEdit 的 ensureWidgetVisible 默认只照顾光标；这里验收整个控件。
            parent.ensureVisible(center.x(), center.y(), widget.width() // 2 + 4, widget.height() // 2 + 4)
            qt_app.processEvents()
            rect = QRect(widget.mapTo(parent.viewport(), QPoint()), widget.size())
            assert parent.viewport().rect().contains(rect), (widget.objectName(), rect)
            break
        parent = parent.parentWidget()
    point = widget.mapTo(window, widget.rect().center())
    hit = window.childAt(point)
    assert hit is widget or (hit is not None and widget.isAncestorOf(hit)), (widget, hit)
    return hit, hit.mapFrom(window, point)


@pytest.mark.parametrize("width,height", [(1280, 780), (1554, 930)])
@pytest.mark.parametrize("page_id", [0, 1])
def test_scrolled_detail_preview_controls_are_visible_and_play_real_media(
    window, qt_app, tmp_path, width, height, page_id
):
    import cv2
    import numpy as np
    from gui.widgets.media_preview import HAS_MULTIMEDIA
    if not HAS_MULTIMEDIA:
        pytest.skip("Qt Multimedia 不可用")
    path = tmp_path / "实际播放.mp4"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 12, (160, 120))
    assert writer.isOpened()
    for i in range(120):
        writer.write(np.full((120, 160, 3), i, dtype=np.uint8))
    writer.release()
    row = window.service.media_registry.register(str(path))
    window.service.db.upsert_video({**row, "status": "analyzed", "category": "B-Roll",
        "summary": "人工测试合成素材，验证完整详情与播放控件", "tags": ["合成素材"],
        "transcription": "用于验证滚动区域中的真实转录字段"})
    window.service.replace_work_scope([str(path)], scan=False)
    window.resize(width, height)
    window.show()
    window.switch_page(page_id)
    page = window.content_stack.currentWidget()
    panel = page.detail_panel
    preview = panel.media_preview
    _wait_for(qt_app, lambda: page.model.rowCount() == 1 and not page._page_loading)
    page.table_view.selectRow(0)
    try:
        _wait_for(qt_app, lambda: isinstance(panel.current_video, dict)
                  and panel.current_video.get("path") == str(path))
        _wait_for(qt_app, lambda: preview._player is not None and preview._player.duration() > 0)
        _wait_for(qt_app, lambda: panel.content_scroll.horizontalScrollBar().maximum() == 0)
        controls = (preview.seek_slider, preview.time_label, preview.play_btn,
                    preview.volume_slider, preview.speed_combo, preview.reveal_btn)
        rects = [QRect(widget.mapTo(preview, QPoint()), widget.size()) for widget in controls]
        for widget, rect in zip(controls, rects):
            assert preview.rect().contains(rect), (widget, preview.rect(), rect)
        for i, rect in enumerate(rects):
            assert all(not rect.intersects(other) for other in rects[i + 1:])
        for button in (preview.play_btn, preview.reveal_btn):
            assert button.width() >= button.sizeHint().width()
        target, point = _reachable_point(window, preview.play_btn, qt_app)
        QTest.mouseClick(target, Qt.LeftButton, pos=point)
        _wait_for(qt_app, lambda: preview._player.position() > 200)
        QTest.mouseClick(target, Qt.LeftButton, pos=point)
        assert preview.play_btn.text() == "播放"
        target, point = _reachable_point(window, preview.speed_combo, qt_app)
        QTest.mouseClick(target, Qt.LeftButton, pos=point)
        QTest.keyClick(preview.speed_combo, Qt.Key_Down)
        QTest.keyClick(preview.speed_combo, Qt.Key_Return)
        _wait_for(qt_app, lambda: preview._player.playbackRate() == 1.25)
        _reachable_point(window, panel.summary_input, qt_app)
        _reachable_point(window, panel.save_btn, qt_app)
        assert panel.save_btn.isEnabled()
    finally:
        preview.set_media(None)
