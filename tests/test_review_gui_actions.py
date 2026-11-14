"""全项目审查：真实入口的操作门禁、标签清空与线程退出回归。"""
import threading
import time
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QThread
from PySide6.QtWidgets import QMessageBox, QWidget

from core.manual_test_environment import wait_for_background_work
from core.video_organizer_service import SettingsManager, VideoOrganizerService
from gui.views.material_library import MaterialLibraryView
from gui.views.workstation import WorkstationView
from gui.widgets.detail_panel import DetailPanel


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setattr('core.video_organizer_service.TAG_CONFIG_FILE', str(tmp_path / 'tags.json'))
    monkeypatch.setattr('core.video_organizer_service.get_settings_file_path', lambda: str(tmp_path / 'settings.json'))
    monkeypatch.setattr(QMessageBox, 'information', lambda *args, **kwargs: None)
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args, **kwargs: None)
    return VideoOrganizerService(settings=SettingsManager.deep_copy_defaults(), on_log=lambda _: None,
        db_path=str(tmp_path / 'catalog.db'), results_json=str(tmp_path / 'results.json'),
        results_csv=str(tmp_path / 'results.csv'))


@pytest.fixture
def widgets(qt_app):
    created = []
    yield created
    for widget in reversed(created):
        for thread in widget.findChildren(QThread):
            assert thread.wait(5000), '不得销毁仍运行的测试线程'
        widget.close()
        widget.deleteLater()
    qt_app.processEvents()


def insert_video(service, tmp_path, name='clip.mp4'):
    path = str(tmp_path / name)
    service.db.upsert_video({'path': path, 'filename': name, 'status': 'analyzed',
        'tags': ['old-tag'], 'summary': '合成摘要', 'category': 'Other'})
    return path


@pytest.mark.parametrize('batch', [False, True])
@pytest.mark.parametrize('merge', [False, True])
def test_detail_empty_tags_persist_in_real_database(service, tmp_path, widgets, batch, merge):
    paths = [insert_video(service, tmp_path, f'{index}.mp4') for index in range(2 if batch else 1)]
    panel = DetailPanel(service)
    widgets.append(panel)
    rows = [service.db.get_video_by_path(path) for path in paths]
    panel.load_video_data(rows if batch else rows[0])
    panel.merge_tags_cb.setChecked(merge)
    panel.tags_widget.set_tags([])
    panel.save_btn.click()
    for path in paths:
        row = service.db.get_video_by_path(path)
        assert row['tags'] == (['old-tag'] if merge else [])
        assert row['manual_override']


class SignalStub:
    def connect(self, callback):
        pass


@pytest.fixture
def worker_factory(monkeypatch):
    created = []
    class WorkerStub:
        def __init__(self, service, paths, **options):
            self.paths, self.options, self.started = paths, options, False
            for name in ('progress_updated', 'snapshot_updated', 'result_ready', 'task_finished'):
                setattr(self, name, SignalStub())
            created.append(self)
        def isRunning(self):
            return self.started
        def start(self):
            self.started = True
    monkeypatch.setattr('gui.views.workstation.AnalysisWorker', WorkerStub)
    return created


@pytest.mark.parametrize('entry', ['analyze_selected', 'start_analysis'])
@pytest.mark.parametrize('force,auto_xmp', [(False, True), (True, False), (True, True)])
def test_analysis_entries_share_confirmation_and_decline_starts_nothing(
    service, tmp_path, widgets, monkeypatch, worker_factory, entry, force, auto_xmp,
):
    path = insert_video(service, tmp_path)
    service.replace_work_scope([path], scan=False)
    service.settings['processing']['auto_sync_xmp_after_analysis'] = auto_xmp
    view = WorkstationView(service)
    widgets.append(view)
    monkeypatch.setattr(view, '_get_selected_paths', lambda: [path])
    view.force_reanalyze_cb.setChecked(force)
    prompts = []
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: prompts.append(args) or QMessageBox.No)
    getattr(view, entry)()
    assert len(prompts) == 1
    assert ('.xmp' in prompts[0][2]) == auto_xmp
    assert ('强制重新分析' in prompts[0][2]) == force
    assert worker_factory == []
    assert view.worker is None
    assert view.analyze_btn.isEnabled()


def test_context_analysis_without_selection_does_not_fall_back_to_visible(
    service, tmp_path, widgets, monkeypatch, worker_factory,
):
    path = insert_video(service, tmp_path)
    service.replace_work_scope([path], scan=False)
    view = WorkstationView(service)
    widgets.append(view)
    monkeypatch.setattr(view, '_get_selected_paths', lambda: [])
    view.analyze_selected()
    assert worker_factory == []


def test_context_analysis_starts_exact_frozen_selected_paths(
    service, tmp_path, widgets, monkeypatch, worker_factory,
):
    paths = [insert_video(service, tmp_path, f'{index}.mp4') for index in range(2)]
    service.replace_work_scope(paths, scan=False)
    view = WorkstationView(service)
    widgets.append(view)
    monkeypatch.setattr(view, '_get_selected_paths', lambda: paths[:1])
    view.analyze_selected()
    assert len(worker_factory) == 1
    assert worker_factory[0].paths == paths[:1]
    assert worker_factory[0].started
    view.worker = None  # 替身不持有真实线程。


@pytest.mark.parametrize('worker_attribute', ['worker', '_io_worker', 'recovery_worker'])
@pytest.mark.parametrize('entry', ['analyze_selected', 'start_analysis', 'delete_selected',
    'batch_replace_tags_ui', 'start_rename', 'rollback_rename', 'sync_metadata',
    'export_fcpx', 'export_ale', 'start_auto_organize'])
def test_busy_operations_cannot_open_dialog_or_replace_worker(
    service, tmp_path, widgets, monkeypatch, worker_factory, worker_attribute, entry,
):
    view = WorkstationView(service)
    widgets.append(view)
    active = SimpleNamespace(isRunning=lambda: True)
    setattr(view, worker_attribute, active)
    monkeypatch.setattr(view, '_resolve_batch_snapshot', lambda: pytest.fail('忙碌时不能读取并执行目标'))
    monkeypatch.setattr(view, '_get_selected_paths', lambda: pytest.fail('忙碌时不能启动选中分析'))
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: pytest.fail('忙碌时不能请求确认'))
    getattr(view, entry)()
    assert getattr(view, worker_attribute) is active
    assert worker_factory == []
    assert '后台任务' in view.status_label.text()
    setattr(view, worker_attribute, None)


def test_analysis_rechecks_busy_after_confirmation(service, tmp_path, widgets, monkeypatch, worker_factory):
    path = insert_video(service, tmp_path)
    service.replace_work_scope([path], scan=False)
    service.settings['processing']['auto_sync_xmp_after_analysis'] = True
    view = WorkstationView(service)
    widgets.append(view)
    monkeypatch.setattr(view, '_get_selected_paths', lambda: [path])
    active = SimpleNamespace(isRunning=lambda: True)
    def confirm(*args):
        view._io_worker = active
        return QMessageBox.Yes
    monkeypatch.setattr(QMessageBox, 'question', confirm)
    view.analyze_selected()
    assert worker_factory == []
    assert view._io_worker is active
    view._io_worker = None


@pytest.mark.parametrize('worker_attribute', ['worker', '_io_worker', 'recovery_worker'])
def test_library_cannot_accumulate_into_busy_workstation(service, tmp_path, widgets, monkeypatch, worker_attribute):
    window = QWidget()
    widgets.append(window)
    workstation = WorkstationView(service, window)
    window.workstation_page = workstation
    library = MaterialLibraryView(service, window)
    active = SimpleNamespace(isRunning=lambda: True)
    setattr(workstation, worker_attribute, active)
    path = insert_video(service, tmp_path)
    service.replace_work_scope([path], scan=False)
    before = service.get_work_scope_paths()
    monkeypatch.setattr(library, '_resolve_batch_snapshot', lambda: pytest.fail('忙碌时不能累加'))
    library.accumulate_selected_to_scope()
    assert service.get_work_scope_paths() == before
    assert getattr(workstation, worker_attribute) is active
    setattr(workstation, worker_attribute, None)


def test_idle_library_uses_workstation_background_scan(service, tmp_path, widgets, monkeypatch):
    window = QWidget()
    widgets.append(window)
    workstation = WorkstationView(service, window)
    window.workstation_page = workstation
    library = MaterialLibraryView(service, window)
    path = insert_video(service, tmp_path)
    monkeypatch.setattr(library, '_resolve_batch_snapshot', lambda: SimpleNamespace(target_paths=[path]))
    calls = []
    original_append = service.append_work_scope
    def append(paths, *, scan):
        calls.append(('append', scan))
        return original_append(paths, scan=scan)
    monkeypatch.setattr(service, 'append_work_scope', append)
    monkeypatch.setattr(workstation, 'background_scan_work_scope', lambda **kwargs: calls.append(('background', kwargs['reason'])))
    library.accumulate_selected_to_scope()
    assert service.get_work_scope_paths() == [path]
    assert calls == [('append', False), ('background', '素材库累加扫盘')]


def test_library_scan_runs_on_real_background_thread(service, tmp_path, widgets, monkeypatch, qt_app):
    window = QWidget()
    widgets.append(window)
    workstation = WorkstationView(service, window)
    window.workstation_page = workstation
    library = MaterialLibraryView(service, window)
    path = insert_video(service, tmp_path)
    monkeypatch.setattr(library, '_resolve_batch_snapshot', lambda: SimpleNamespace(target_paths=[path]))
    started, release = threading.Event(), threading.Event()
    thread_ids = []
    def scan(progress_callback=None):
        thread_ids.append(threading.get_ident())
        started.set()
        assert release.wait(3)
        return []
    monkeypatch.setattr(service, 'scan_and_register_work_scope', scan)
    try:
        before = time.monotonic()
        library.accumulate_selected_to_scope()
        assert time.monotonic() - before < 1
        assert started.wait(2)
        assert thread_ids != [threading.get_ident()]
        assert workstation._has_active_background_task()
        assert not workstation.analyze_btn.isEnabled()
    finally:
        release.set()
        worker = getattr(workstation, '_io_worker', None)
        if worker is not None:
            assert worker.wait(3000)
        deadline = time.monotonic() + 3
        while getattr(workstation, '_io_worker', None) is not None and time.monotonic() < deadline:
            qt_app.processEvents()
            time.sleep(0.01)
    assert workstation._io_worker is None
    assert workstation.analyze_btn.isEnabled()


def test_main_window_refuses_close_with_real_active_thread(service, widgets, qt_app):
    from gui.main_window import MainWindow
    release, started = threading.Event(), threading.Event()
    class BusyThread(QThread):
        def run(self):
            started.set()
            release.wait(3)
    window = MainWindow(service)
    widgets.append(window)
    window._allow_exit = True
    thread = BusyThread(window.workstation_page)
    window.workstation_page.worker = thread
    thread.start()
    assert started.wait(2)
    try:
        assert not window.close()
        assert thread.isRunning()
        assert not getattr(window, '_close_accepted', False)
    finally:
        release.set()
        assert thread.wait(2000)
        window.workstation_page.worker = None
        qt_app.processEvents()


@pytest.mark.parametrize('finishes', [False, True])
def test_wait_rechecks_real_qthread_until_finish_or_timeout(qt_app, finishes):
    release = threading.Event()
    started = threading.Event()
    class BusyThread(QThread):
        def run(self):
            started.set()
            release.wait(3)
    window = QWidget()
    thread = BusyThread(window)
    window.worker = thread  # 同时通过父子树和属性发现，仍只能每轮去重。
    timer = threading.Timer(0.3, release.set) if finishes else None
    thread.start()
    assert started.wait(2)
    if timer:
        timer.start()
    try:
        before = time.monotonic()
        done = wait_for_background_work(window, timeout=0.6 if finishes else 0.35)
        elapsed = time.monotonic() - before
        assert done == finishes
        assert thread.isRunning() != finishes
        assert elapsed >= (0.25 if finishes else 0.3)
    finally:
        release.set()
        assert thread.wait(2000)
        if timer:
            timer.join(2)
        window.close()
