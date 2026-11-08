"""素材展示回归：合成帧、临时库与离屏Qt。"""
import os
import time
from pathlib import Path
import numpy as np
import pytest
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import Qt, QSize, QPoint
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QGroupBox, QPushButton, QHeaderView
from PySide6.QtTest import QTest
from core.video_organizer_service import VideoProcessor, VideoOrganizerService, SettingsManager
from gui.models.video_table import VideoTableModel, COL_THUMB, COL_FILENAME
from gui.views.workstation import WorkstationView
from gui.views.material_library import MaterialLibraryView
from gui.views.settings import SettingsView

@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])

@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setattr("core.video_organizer_service.TAG_CONFIG_FILE", str(tmp_path / "tags.json"))
    monkeypatch.setattr("core.video_organizer_service.get_settings_file_path", lambda: str(tmp_path / "settings.json"))
    return VideoOrganizerService(settings=SettingsManager.deep_copy_defaults(), db_path=str(tmp_path / "test.db"), on_log=lambda *_: None)

def fake_capture(monkeypatch):
    class Capture:
        def __init__(self, *_): pass
        def get(self, *_): return 1
        def set(self, *_): pass
        def read(self): return True, np.full((48, 80, 3), (25, 80, 220), dtype=np.uint8)
        def release(self): pass
    monkeypatch.setattr("core.video_organizer_service.cv2.VideoCapture", Capture)

def test_thumbnail_written_in_unicode_directory(tmp_path, monkeypatch):
    fake_capture(monkeypatch)
    source=tmp_path / "视频.mp4"
    source.write_bytes(b"fixture")
    processor=VideoProcessor(str(tmp_path / "素材缓存"))
    result=processor.extract_frames(str(source), max_frames=1, save_thumbnail=True, use_scene_detection=False)
    assert result["thumbnail"] and Path(result["thumbnail"]).is_file()
    assert not QImage(result["thumbnail"]).isNull()

@pytest.mark.parametrize("view_type", [WorkstationView, MaterialLibraryView])
def test_filename_width_is_resizable_and_survives_hide(app, service, view_type):
    view=view_type(service)
    try:
        view.resize(1400, 850)
        view.show()
        app.processEvents()
        table=view.table_view
        assert table.horizontalHeader().sectionResizeMode(COL_FILENAME)==QHeaderView.Interactive
        table.setColumnWidth(COL_FILENAME, 317)
        app.processEvents()
        assert table.columnWidth(COL_FILENAME)==317
        table.setColumnHidden(COL_FILENAME, True)
        app.processEvents()
        assert service.settings["ui_preferences"]["table_column_widths"][str(COL_FILENAME)]==317
        table.setColumnHidden(COL_FILENAME, False)
        app.processEvents()
        assert table.columnWidth(COL_FILENAME)==317
    finally:
        view.close()

def test_settings_only_retains_search(app, service):
    view=SettingsView(service)
    try:
        assert not any(box.title()=="设置目录" for box in view.findChildren(QGroupBox))
        assert not any(b.text()=="日常偏好" for b in view.findChildren(QPushButton))
        view.settings_search_input.setText("主题")
        assert view.settings_search_results.model().rowCount()>0
        assert view.focus_setting("theme")
    finally:
        view.prefs_controller.cancel()
        view.close()


def wait_thumbnail(app, model, index):
    model.data(index, Qt.DecorationRole)
    deadline=time.monotonic()+3
    while model.loading_paths and time.monotonic()<deadline:
        app.processEvents()
        QTest.qWait(10)
    assert not model.loading_paths
    return model.data(index, Qt.DecorationRole)

@pytest.mark.parametrize("cache_state", ["missing", "corrupt", "no_reference"])
def test_existing_missing_cache_recovers_without_ai(app, tmp_path, monkeypatch, cache_state):
    fake_capture(monkeypatch)
    source=tmp_path / "视频.mp4"
    source.write_bytes(b"fixture")
    cache=tmp_path / "缓存" / "old.webp"
    cache.parent.mkdir()
    if cache_state=="corrupt": cache.write_bytes(b"broken")
    row={"path":str(source), "filename":source.name}
    if cache_state!="no_reference": row["thumbnail_path"]=str(cache)
    model=VideoTableModel([row], thumbnail_dir=str(cache.parent))
    index=model.index(0, COL_THUMB)
    pixmap=wait_thumbnail(app, model, index)
    assert pixmap is not None and not pixmap.isNull()
    assert Path(model._thumbnail_path(row)).is_file()
    model.thumbnail_pool.waitForDone()


def test_missing_video_finishes_and_can_retry_after_refresh(app, tmp_path, monkeypatch):
    cache=tmp_path / "missing.webp"
    row={"path":str(tmp_path / "missing.mp4"), "thumbnail_path":str(cache)}
    model=VideoTableModel([row])
    index=model.index(0, COL_THUMB)
    assert wait_thumbnail(app, model, index).isNull()
    assert not model.loading_paths
    model.update_data([row])
    image=QImage(80,48,QImage.Format_RGB32)
    image.fill(Qt.red)
    assert image.save(str(cache))
    assert not wait_thumbnail(app, model, model.index(0,COL_THUMB)).isNull()
    model.thumbnail_pool.waitForDone()


def test_encoder_failure_does_not_return_nonexistent_path(tmp_path, monkeypatch):
    fake_capture(monkeypatch)
    source=tmp_path / "video.mp4"
    source.write_bytes(b"fixture")
    monkeypatch.setattr("core.video_organizer_service.cv2.imencode", lambda *_: (False,None))
    result=VideoProcessor(str(tmp_path / "cache")).extract_frames(str(source), max_frames=1, save_thumbnail=True, use_scene_detection=False)
    assert result["thumbnail"] is None

@pytest.mark.parametrize("view_type", [WorkstationView, MaterialLibraryView])
def test_header_drag_is_debounced_and_persisted(app, service, monkeypatch, view_type):
    saves=[]
    original=SettingsManager.save_settings
    view=view_type(service)
    try:
        view.resize(1800,900)
        view.show()
        app.processEvents()
        monkeypatch.setattr(SettingsManager,"save_settings",staticmethod(lambda *args,**kw: saves.append(1) or original(*args,**kw)))
        header=view.table_view.horizontalHeader()
        for column in (COL_FILENAME, 4):
            old=header.sectionSize(column)
            start=QPoint(header.sectionViewportPosition(column)+old-1,header.height()//2)
            QTest.mousePress(header.viewport(),Qt.LeftButton,Qt.NoModifier,start)
            QTest.mouseMove(header.viewport(),start+QPoint(43,0),5)
            QTest.mouseRelease(header.viewport(),Qt.LeftButton,Qt.NoModifier,start+QPoint(43,0))
            assert header.sectionSize(column)>old+30
        assert saves==[]
        view.column_prefs_controller.flush()
        assert saves==[1]
        saved=header.sectionSize(COL_FILENAME)
        reopened=view_type(service)
        try:
            assert reopened.table_view.columnWidth(COL_FILENAME)==saved
        finally:
            reopened.close()
    finally:
        view.column_prefs_controller.cancel()
        view.close()
