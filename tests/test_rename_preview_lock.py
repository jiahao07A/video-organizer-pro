"""真实Windows/Qt媒体预览与工作台重命名的集成回归，仅使用合成素材。"""
import os
import time
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from PySide6.QtCore import QTimer
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QMessageBox

from core.manual_test_environment import wait_for_background_work
from core.video_organizer_service import SettingsManager, VideoOrganizerService
from gui.views.workstation import WorkstationView


def wait_until(app, predicate, seconds=8):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        QTest.qWait(20)
    assert predicate(), "Qt媒体或后台任务未在期限内就绪"


@pytest.fixture
def scene(qt_app, tmp_path, monkeypatch):
    monkeypatch.setattr('core.video_organizer_service.TAG_CONFIG_FILE', str(tmp_path / 'tags.json'))
    monkeypatch.setattr('core.video_organizer_service.get_settings_file_path', lambda: str(tmp_path / 'settings.json'))
    for name in ('information', 'warning', 'critical'):
        monkeypatch.setattr(QMessageBox, name, lambda *args, **kwargs: None)
    monkeypatch.setattr(QMessageBox, 'question', lambda *args, **kwargs: QMessageBox.Yes)
    service = VideoOrganizerService(settings=SettingsManager.deep_copy_defaults(), on_log=lambda _: None,
        db_path=str(tmp_path / 'catalog.db'), results_json=str(tmp_path / 'results.json'))
    path = tmp_path / '合成预览.mp4'
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'mp4v'), 12, (160, 120))
    assert writer.isOpened()
    for index in range(120):
        writer.write(np.full((120, 160, 3), index, dtype=np.uint8))
    writer.release()
    row = service.media_registry.register(str(path))
    service.db.upsert_video({**row, 'status': 'analyzed', 'category': 'Other',
        'summary': 'synthetic preview', 'tags': ['test']})
    service.replace_work_scope([str(path)], scan=False)
    view = WorkstationView(service)
    assert wait_for_background_work(view, timeout=8)
    monkeypatch.setattr(view, '_resolve_batch_snapshot', lambda: SimpleNamespace(
        target_paths=(str(path),), scope_paths=(str(path),)))

    class AcceptedDialog:
        active_conflict_count = 0
        excluded_count = 0
        def __init__(self, *args):
            pass
        def exec(self):
            return QDialog.Accepted
        def get_final_config(self):
            return {'pattern': 'new-{original_name}', 'find_regex': None,
                    'replace_str': '', 'preview_date': '20261114'}
        def selected_source_paths(self):
            return [str(path)]

    monkeypatch.setattr('gui.views.workstation.BatchRenameDialog', AcceptedDialog)
    state = SimpleNamespace(service=service, view=view, path=path,
                            preview=view.detail_panel.media_preview, dialog=AcceptedDialog)
    yield state
    state.preview.set_media(None)
    assert wait_for_background_work(view, timeout=8)
    for timer in view.findChildren(QTimer):
        timer.stop()
    view.close()
    view.deleteLater()
    qt_app.processEvents()


@pytest.mark.skipif(os.name != 'nt', reason='真实Windows文件共享锁')
@pytest.mark.parametrize('playback', ['loaded', 'playing', 'paused', 'stopped'])
def test_workstation_rename_releases_its_loaded_media(scene, qt_app, playback):
    scene.preview.set_media(str(scene.path))
    player = scene.preview._player
    wait_until(qt_app, lambda: player.duration() > 0)
    if playback != 'loaded':
        player.play()
        wait_until(qt_app, lambda: player.playbackState() == QMediaPlayer.PlayingState)
        if playback == 'paused':
            player.pause()
        elif playback == 'stopped':
            scene.preview.stop()
    original = scene.path.read_bytes()
    xmp = scene.path.with_suffix('.xmp')
    xmp.write_bytes(b'synthetic sidecar')
    scene.view.start_rename(dry_run=False)
    wait_until(qt_app, lambda: not scene.view.worker.isRunning())
    qt_app.processEvents()
    result = scene.service._last_rename_result
    assert result['success_count'] == 1, result['failed_items']
    target = scene.path.with_name('new-' + scene.path.name)
    assert not scene.path.exists() and target.read_bytes() == original
    assert target.with_suffix('.xmp').read_bytes() == b'synthetic sidecar'
    assert not xmp.exists()
    assert scene.service.db.get_video_by_path(str(target)) is not None
    assert scene.service.get_work_scope_paths() == [str(target)]


def test_control_unloading_media_before_rename_releases_windows_handle(scene, qt_app):
    scene.preview.set_media(str(scene.path))
    wait_until(qt_app, lambda: scene.preview._player.duration() > 0)
    scene.preview.set_media(None)
    scene.view.start_rename(dry_run=False)
    wait_until(qt_app, lambda: not scene.view.worker.isRunning())
    assert scene.service._last_rename_result['success_count'] == 1


def test_simulated_rename_keeps_real_media_loaded_and_source_unchanged(scene, qt_app):
    scene.preview.set_media(str(scene.path))
    wait_until(qt_app, lambda: scene.preview._player.duration() > 0)
    source = scene.preview._player.source()
    original = scene.path.read_bytes()
    scene.view.start_rename(dry_run=True)
    wait_until(qt_app, lambda: not scene.view.worker.isRunning())
    qt_app.processEvents()
    assert scene.preview._path == str(scene.path)
    assert scene.preview._player.source() == source
    assert scene.path.read_bytes() == original
    assert not scene.path.with_name('new-' + scene.path.name).exists()
    assert scene.service.db.get_video_by_path(str(scene.path)) is not None
    assert scene.service.get_work_scope_paths() == [str(scene.path)]


@pytest.mark.parametrize('reject_at', ['preview', 'final_confirmation', 'busy_during_confirmation'])
def test_rejected_or_busy_rename_keeps_real_media_loaded(scene, qt_app, monkeypatch, reject_at):
    scene.preview.set_media(str(scene.path))
    wait_until(qt_app, lambda: scene.preview._player.duration() > 0)
    source = scene.preview._player.source()
    original = scene.path.read_bytes()
    before_worker = getattr(scene.view, 'worker', None)
    if reject_at == 'preview':
        monkeypatch.setattr(scene.dialog, 'exec', lambda _: QDialog.Rejected)
    elif reject_at == 'final_confirmation':
        monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.No)
    else:
        def confirm(*a, **k):
            assert scene.service._analysis_run_lock.acquire(blocking=False)
            return QMessageBox.Yes
        monkeypatch.setattr(QMessageBox, 'question', confirm)
    try:
        scene.view.start_rename(dry_run=False)
        qt_app.processEvents()
        assert getattr(scene.view, 'worker', None) is before_worker
        assert scene.preview._path == str(scene.path)
        assert scene.preview._player.source() == source
        assert scene.path.read_bytes() == original
        assert scene.service.db.get_video_by_path(str(scene.path)) is not None
        assert scene.service.get_work_scope_paths() == [str(scene.path)]
        assert not scene.path.with_name('new-' + scene.path.name).exists()
    finally:
        if scene.service._analysis_run_lock.locked():
            scene.service._analysis_run_lock.release()


def test_renaming_other_target_keeps_unselected_real_preview(scene, qt_app):
    other = scene.path.with_name('未选预览.mp4')
    other.write_bytes(scene.path.read_bytes())
    scene.preview.set_media(str(other))
    wait_until(qt_app, lambda: scene.preview._player.duration() > 0)
    source = scene.preview._player.source()
    original = other.read_bytes()
    scene.view.start_rename(dry_run=False)
    wait_until(qt_app, lambda: not scene.view.worker.isRunning())
    qt_app.processEvents()
    assert scene.service._last_rename_result['success_count'] == 1
    assert scene.preview._path == str(other)
    assert scene.preview._player.source() == source
    assert other.read_bytes() == original
    assert scene.path.with_name('new-' + scene.path.name).exists()


@pytest.mark.skipif(os.name != 'nt', reason='真实Windows文件共享锁')
def test_external_windows_lock_fails_safely_then_release_allows_retry(scene, qt_app):
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
        wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    # 允许读取和写入，但不共享删除权限，模拟真实外部程序的重命名阻止句柄。
    handle = kernel32.CreateFileW(str(scene.path), 0x80000000, 0x1 | 0x2,
        None, 3, 0x80, None)
    assert handle != wintypes.HANDLE(-1).value, ctypes.WinError(ctypes.get_last_error())
    original = scene.path.read_bytes()
    xmp = scene.path.with_suffix('.xmp')
    xmp.write_bytes(b'external lock synthetic XMP')
    target = scene.path.with_name('new-' + scene.path.name)
    before_row = scene.service.db.get_video_by_path(str(scene.path))
    try:
        scene.preview.set_media(str(scene.path))
        wait_until(qt_app, lambda: scene.preview._player.duration() > 0)
        scene.view.start_rename(dry_run=False)
        wait_until(qt_app, lambda: not scene.view.worker.isRunning())
        qt_app.processEvents()
        result = scene.service._last_rename_result
        assert result['success_count'] == 0 and result['failed_count'] == 1
        assert 'WinError 32' in result['failed_items'][0]['reason']
        assert scene.preview._path is None and scene.preview._player.source().isEmpty()
        assert scene.path.read_bytes() == original and not target.exists()
        assert xmp.read_bytes() == b'external lock synthetic XMP'
        assert not target.with_suffix('.xmp').exists()
        assert scene.service.db.get_video_by_path(str(scene.path)) == before_row
        assert scene.service.db.get_video_by_path(str(target)) is None
        assert scene.service.get_work_scope_paths() == [str(scene.path)]
    finally:
        assert kernel32.CloseHandle(handle)
    assert wait_for_background_work(scene.view, timeout=8)
    scene.view.start_rename(dry_run=False)
    wait_until(qt_app, lambda: not scene.view.worker.isRunning())
    qt_app.processEvents()
    assert scene.service._last_rename_result['success_count'] == 1
    assert not scene.path.exists() and target.read_bytes() == original
    assert target.with_suffix('.xmp').read_bytes() == b'external lock synthetic XMP'
    assert scene.service.db.get_video_by_path(str(scene.path)) is None
    assert scene.service.db.get_video_by_path(str(target)) is not None
    assert scene.service.get_work_scope_paths() == [str(target)]
