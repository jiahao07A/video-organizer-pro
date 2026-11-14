"""R2：手动400恢复中央入口检查页面、跨页任务及确认期间竞态。"""
import threading

import pytest
from PySide6.QtCore import QThread
from PySide6.QtWidgets import QDialog, QMessageBox, QWidget

from core.manual_test_environment import wait_for_background_work
from core.video_organizer_service import SettingsManager, VideoOrganizerService
from gui.views.material_library import MaterialLibraryView
from gui.views.workstation import WorkstationView
from gui.widgets.recovery_controls import launch_manual_recovery


@pytest.fixture
def scene(qt_app, tmp_path, monkeypatch):
    monkeypatch.setattr('core.video_organizer_service.TAG_CONFIG_FILE', str(tmp_path / 'tags.json'))
    monkeypatch.setattr('core.video_organizer_service.get_settings_file_path', lambda: str(tmp_path / 'settings.json'))
    monkeypatch.setattr(QMessageBox, 'information', lambda *args, **kwargs: None)
    service = VideoOrganizerService(settings=SettingsManager.deep_copy_defaults(), on_log=lambda _: None,
        db_path=str(tmp_path / 'catalog.db'), results_json=str(tmp_path / 'r.json'), results_csv=str(tmp_path / 'r.csv'))
    path = tmp_path / 'clip.mp4'
    path.write_bytes(b'synthetic-local-video')
    service.db.upsert_video({'path': str(path), 'filename': path.name, 'status': 'pending'})
    service.replace_work_scope([str(path)], scan=False)
    monkeypatch.setattr(service, 'recovery_target_paths', lambda paths: paths)
    window = QWidget()
    workstation = WorkstationView(service, window)
    window.workstation_page = workstation
    library = MaterialLibraryView(service, window)
    for view in (workstation, library):
        monkeypatch.setattr(view, '_resolve_batch_snapshot', lambda: service.resolve_operation_target_snapshot(
            [str(path)], [str(path)], scope_paths=None))
    yield service, str(path), window, workstation, library
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


@pytest.fixture
def recovery_ui(monkeypatch):
    created, dialogs = [], []
    action = {'on_confirm': lambda: None, 'accepted': True}
    class Signal:
        def connect(self, callback):
            pass
    class Worker:
        def __init__(self, service, paths, **kwargs):
            self.paths, self.options, self.started = paths, kwargs, False
            self.task_finished = Signal()
            self.progress_updated = Signal()
            self.snapshot_updated = Signal()
            created.append(self)
        def start(self):
            self.started = True
        def isRunning(self):
            return False  # 不运行分析，门禁仅通过实际BusyThread验证。
    class Dialog:
        def __init__(self, service, paths, owner):
            self.paths, self.options = paths, {'recovery_mode': 'primary'}
            dialogs.append(self)
        def exec(self):
            action['on_confirm']()
            return QDialog.Accepted if action['accepted'] else QDialog.Rejected
    monkeypatch.setattr('gui.workers.analysis_worker.AnalysisWorker', Worker)
    monkeypatch.setattr('gui.widgets.recovery_controls.ManualRecoveryDialog', Dialog)
    return created, dialogs, action


@pytest.mark.parametrize('entry', ['workstation', 'library'])
@pytest.mark.parametrize('worker_attribute', ['worker', '_io_worker', 'recovery_worker'])
@pytest.mark.parametrize('during_confirmation', [False, True])
def test_central_recovery_checks_real_workstation_thread_before_and_after_confirmation(
    scene, recovery_ui, entry, worker_attribute, during_confirmation,
):
    service, path, window, workstation, library = scene
    created, dialogs, action = recovery_ui
    view = workstation if entry == 'workstation' else library
    thread = BusyThread(workstation)
    before = service.get_work_scope_paths()
    def start():
        setattr(workstation, worker_attribute, thread)
        thread.start()
        assert thread.entered.wait(2)
    if during_confirmation:
        action['on_confirm'] = start
    else:
        start()
    try:
        launch_manual_recovery(view)
        assert created == []
        assert len(dialogs) == int(during_confirmation)
        assert thread.isRunning()
        assert getattr(workstation, worker_attribute) is thread
        assert getattr(view, 'recovery_worker', None) is (thread if view is workstation and worker_attribute == 'recovery_worker' else None)
        assert service.db.get_video_by_path(path)['status'] == 'pending'
        assert service.get_work_scope_paths() == before
    finally:
        thread.release.set()
        assert thread.wait(2000)
        setattr(workstation, worker_attribute, None)


@pytest.mark.parametrize('worker_attribute', ['worker', '_io_worker', 'recovery_worker'])
def test_central_recovery_checks_library_own_active_thread(scene, recovery_ui, worker_attribute):
    service, path, window, workstation, library = scene
    created, dialogs, action = recovery_ui
    thread = BusyThread(library)
    setattr(library, worker_attribute, thread)
    thread.start()
    assert thread.entered.wait(2)
    try:
        launch_manual_recovery(library)
        assert created == dialogs == []
        assert getattr(library, worker_attribute) is thread
    finally:
        thread.release.set()
        assert thread.wait(2000)
        setattr(library, worker_attribute, None)


@pytest.mark.parametrize('during_confirmation', [False, True])
def test_central_recovery_rechecks_service_analysis_lock(scene, recovery_ui, during_confirmation):
    service, path, window, workstation, library = scene
    created, dialogs, action = recovery_ui
    lock = service._analysis_run_lock
    if during_confirmation:
        action['on_confirm'] = lock.acquire
    else:
        lock.acquire()
    try:
        launch_manual_recovery(library)
        assert created == []
        assert len(dialogs) == int(during_confirmation)
    finally:
        lock.release()


@pytest.mark.parametrize('accepted', [False, True])
def test_idle_recovery_starts_once_only_when_accepted(scene, recovery_ui, accepted):
    service, path, window, workstation, library = scene
    created, dialogs, action = recovery_ui
    action['accepted'] = accepted
    launch_manual_recovery(workstation)
    assert len(dialogs) == 1
    assert len(created) == int(accepted)
    if accepted:
        assert created[0].paths == [path]
        assert created[0].started
        assert workstation.worker is workstation.recovery_worker is created[0]
        workstation.worker = workstation.recovery_worker = None


def test_read_only_catalog_query_does_not_block_manual_recovery(scene, recovery_ui):
    service, path, window, workstation, library = scene
    created, dialogs, action = recovery_ui
    thread = BusyThread(library)
    library._catalog_worker = thread
    thread.start()
    assert thread.entered.wait(2)
    try:
        launch_manual_recovery(library)
        assert len(created) == 1
        assert created[0].started
        assert library.recovery_worker is created[0]
        assert thread.isRunning()
    finally:
        thread.release.set()
        assert thread.wait(2000)
        library._catalog_worker = library.recovery_worker = None
