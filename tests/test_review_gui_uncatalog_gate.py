"""R2：素材库取消入库必须尊重工作台实际后台线程。"""
import threading

import pytest
from PySide6.QtCore import QThread
from PySide6.QtWidgets import QMessageBox, QWidget

from core.manual_test_environment import wait_for_background_work
from core.video_organizer_service import SettingsManager, VideoOrganizerService
from gui.views.material_library import MaterialLibraryView
from gui.views.workstation import WorkstationView


@pytest.fixture
def scene(qt_app, tmp_path, monkeypatch):
    monkeypatch.setattr('core.video_organizer_service.TAG_CONFIG_FILE', str(tmp_path / 'tags.json'))
    monkeypatch.setattr('core.video_organizer_service.get_settings_file_path', lambda: str(tmp_path / 'settings.json'))
    service = VideoOrganizerService(settings=SettingsManager.deep_copy_defaults(), on_log=lambda _: None,
        db_path=str(tmp_path / 'catalog.db'), results_json=str(tmp_path / 'r.json'), results_csv=str(tmp_path / 'r.csv'))
    paths = []
    for index in range(2):
        path = tmp_path / f'{index}.mp4'
        path.write_bytes(f'synthetic-{index}'.encode())
        paths.append(str(path))
        service.db.upsert_video({'path': str(path), 'filename': path.name, 'status': 'pending'})
    service.replace_work_scope(paths, scan=False)
    window = QWidget()
    workstation = WorkstationView(service, window)
    window.workstation_page = workstation
    library = MaterialLibraryView(service, window)
    monkeypatch.setattr(library, '_resolve_batch_snapshot', lambda: service.resolve_operation_target_snapshot(
        paths[:1], paths, scope_paths=None))
    yield service, paths, window, workstation, library
    assert wait_for_background_work(window, timeout=5)
    window.close()
    window.deleteLater()
    qt_app.processEvents()


class BusyThread(QThread):
    def __init__(self, parent):
        super().__init__(parent)
        self.release = threading.Event()
        self.entered = threading.Event()

    def run(self):
        self.entered.set()
        self.release.wait(5)


@pytest.mark.parametrize('worker_attribute', ['worker', '_io_worker', 'recovery_worker'])
@pytest.mark.parametrize('start_during_confirmation', [False, True])
def test_uncatalog_preserves_database_and_scope_with_real_running_thread(
    scene, monkeypatch, worker_attribute, start_during_confirmation,
):
    service, paths, window, workstation, library = scene
    thread = BusyThread(workstation)
    before = service.get_work_scope_paths()
    prompts = []
    def start_thread():
        setattr(workstation, worker_attribute, thread)
        thread.start()
        assert thread.entered.wait(2)
    def confirm(*args, **kwargs):
        prompts.append(args)
        if start_during_confirmation:
            start_thread()
        return QMessageBox.Yes
    monkeypatch.setattr(QMessageBox, 'question', confirm)
    if not start_during_confirmation:
        start_thread()
    try:
        library.uncatalog_selected()
        assert thread.isRunning()
        assert getattr(workstation, worker_attribute) is thread
        assert service.get_work_scope_paths() == before
        assert all(service.db.get_video_by_path(path) is not None for path in paths)
        assert len(prompts) == int(start_during_confirmation)
        assert '后台任务' in library.status_label.text()
    finally:
        thread.release.set()
        assert thread.wait(2000)
        setattr(workstation, worker_attribute, None)


@pytest.mark.parametrize('accepted', [False, True])
def test_idle_uncatalog_keeps_confirmation_and_file_semantics(scene, monkeypatch, accepted):
    from pathlib import Path
    service, paths, window, workstation, library = scene
    prompts = []
    def confirm(*args, **kwargs):
        prompts.append(args)
        return QMessageBox.Yes if accepted else QMessageBox.No
    monkeypatch.setattr(QMessageBox, 'question', confirm)
    library.uncatalog_selected()
    assert len(prompts) == 1
    assert (service.db.get_video_by_path(paths[0]) is None) == accepted
    assert service.db.get_video_by_path(paths[1]) is not None
    # 取消入库只移除库记录，保留显式路径范围，原有产品语义不变。
    assert service.get_work_scope_paths() == paths
    assert all(Path(path).exists() for path in paths)


@pytest.mark.parametrize('worker_attribute', ['worker', '_io_worker', 'recovery_worker'])
@pytest.mark.parametrize('operation,start_during_confirmation', [
    ('uncatalog', False), ('uncatalog', True), ('accumulate', False),
])
def test_library_own_execution_threads_preserve_records_and_scope(
    scene, monkeypatch, worker_attribute, operation, start_during_confirmation,
):
    service, paths, window, workstation, library = scene
    service.replace_work_scope(paths[:1], scan=False)
    monkeypatch.setattr(library, '_resolve_batch_snapshot', lambda: service.resolve_operation_target_snapshot(
        paths[1:], paths, scope_paths=None))
    scans = []
    monkeypatch.setattr(workstation, 'background_scan_work_scope', lambda **kwargs: scans.append(kwargs))
    monkeypatch.setattr(QMessageBox, 'information', lambda *args: None)
    thread = BusyThread(library)
    before = service.get_work_scope_paths()
    prompts = []
    def start_thread():
        setattr(library, worker_attribute, thread)
        thread.start()
        assert thread.entered.wait(2)
    def confirm(*args, **kwargs):
        prompts.append(args)
        if start_during_confirmation:
            start_thread()
        return QMessageBox.Yes
    monkeypatch.setattr(QMessageBox, 'question', confirm)
    if not start_during_confirmation:
        start_thread()
    try:
        if operation == 'uncatalog':
            library.uncatalog_selected()
        else:
            library.accumulate_selected_to_scope()
        assert thread.isRunning()
        assert getattr(library, worker_attribute) is thread
        assert service.get_work_scope_paths() == before
        assert all(service.db.get_video_by_path(path) is not None for path in paths)
        assert len(prompts) == int(start_during_confirmation)
        assert scans == []
        assert '后台任务' in library.status_label.text()
    finally:
        thread.release.set()
        assert thread.wait(2000)
        setattr(library, worker_attribute, None)


@pytest.mark.parametrize('operation,start_during_confirmation', [
    ('uncatalog', False), ('uncatalog', True), ('accumulate', False),
])
def test_library_service_analysis_lock_blocks_writes(scene, monkeypatch, operation, start_during_confirmation):
    service, paths, window, workstation, library = scene
    service.replace_work_scope(paths[:1], scan=False)
    monkeypatch.setattr(library, '_resolve_batch_snapshot', lambda: service.resolve_operation_target_snapshot(
        paths[1:], paths, scope_paths=None))
    scans = []
    monkeypatch.setattr(workstation, 'background_scan_work_scope', lambda **kwargs: scans.append(kwargs))
    monkeypatch.setattr(QMessageBox, 'information', lambda *args: None)
    lock = service._analysis_run_lock
    prompts = []
    def confirm(*args, **kwargs):
        prompts.append(args)
        if start_during_confirmation:
            assert lock.acquire(blocking=False)
        return QMessageBox.Yes
    monkeypatch.setattr(QMessageBox, 'question', confirm)
    if not start_during_confirmation:
        assert lock.acquire(blocking=False)
    before = service.get_work_scope_paths()
    try:
        if operation == 'uncatalog':
            library.uncatalog_selected()
        else:
            library.accumulate_selected_to_scope()
        assert lock.locked()
        assert service.get_work_scope_paths() == before
        assert all(service.db.get_video_by_path(path) is not None for path in paths)
        assert scans == []
        assert len(prompts) == int(start_during_confirmation)
        assert '后台任务' in library.status_label.text()
    finally:
        if lock.locked():
            lock.release()


@pytest.mark.parametrize('worker_attribute', ['_detail_worker', '_query_worker'])
def test_library_read_only_threads_do_not_block_idle_uncatalog(scene, monkeypatch, worker_attribute):
    service, paths, window, workstation, library = scene
    thread = BusyThread(library)
    setattr(library, worker_attribute, thread)
    monkeypatch.setattr(QMessageBox, 'question', lambda *args, **kwargs: QMessageBox.Yes)
    thread.start()
    assert thread.entered.wait(2)
    try:
        library.uncatalog_selected()
        assert thread.isRunning()
        assert service.db.get_video_by_path(paths[0]) is None
        assert service.db.get_video_by_path(paths[1]) is not None
    finally:
        thread.release.set()
        assert thread.wait(2000)
        setattr(library, worker_attribute, None)
