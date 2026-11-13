"""监控闭环回归：真实临时库与文件；不调用外部 AI、不写登录项。"""
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from core.video_organizer_service import SettingsManager, VideoOrganizerService
from core.media_watch import MediaDirectoryMonitor
from core.watch_config import normalize_watch_config, effective_directory_options, is_path_watchable
from core.processing_queue import ProcessingQueue


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setattr('core.video_organizer_service.TAG_CONFIG_FILE', str(tmp_path / 'tags.json'))
    monkeypatch.setattr('core.video_organizer_service.get_settings_file_path', lambda: str(tmp_path / 'settings.json'))
    return VideoOrganizerService(settings=SettingsManager.deep_copy_defaults(), on_log=lambda _: None,
        db_path=str(tmp_path / 'catalog.db'), results_json=str(tmp_path / 'results.json'), results_csv=str(tmp_path / 'results.csv'))


def test_all_processing_entrypoints_reject_duplicate(service, tmp_path, monkeypatch):
    original, copy = tmp_path / 'a.mp4', tmp_path / 'b.mp4'
    original.write_bytes(b'full matching bytes')
    copy.write_bytes(original.read_bytes())
    first = service.media_registry.register(str(original))
    second = service.media_registry.register(str(copy), source='watch', task_id='task')
    assert second['duplicate_of'] == first['id']
    def forbidden(*args, **kwargs):
        pytest.fail('重复副本触发了分析／打标／文件处理')
    monkeypatch.setattr(service, '_process_single_video', forbidden)
    monkeypatch.setattr(service.processor, 'extract_frames', forbidden)
    monkeypatch.setattr(service.ai, 'analyze_video', forbidden)
    result = service.run_analysis([str(copy)], force_reanalyze=True)
    assert result['attempted'] == 0 and result['duplicate_skipped'] == 1
    retry = service.preview_manual_retry([str(copy)])
    assert not retry['paths']
    assert service.batch_rename(selected_paths=[str(copy)]) == []
    xmp = service.sync_metadata_to_xmp([str(copy)], return_details=True)
    assert xmp['success_count'] == 0
    assert not copy.with_suffix('.xmp').exists()
    target = tmp_path / 'export.fcpxml'
    assert not service.export_to_fcpx_xml(str(target), [str(copy)])
    assert not target.exists()
    assert not service.export_to_ale(str(tmp_path / 'export.ale'), [str(copy)])
    with pytest.raises(ValueError, match='重复副本'):
        service.update_video_metadata(str(copy), {'tags': ['forbidden']})
    assert service.db.get_video_by_path(str(copy))['tags'] == []


def test_monitor_duplicate_batch_completes_without_analysis(service, tmp_path, monkeypatch):
    root = tmp_path / 'watched'
    root.mkdir()
    original = tmp_path / 'original.mp4'
    copy = root / 'copy.mp4'
    original.write_bytes(b'same')
    copy.write_bytes(original.read_bytes())
    service.media_registry.register(str(original))
    config = normalize_watch_config({'directories': [{'path': str(root)}]})
    monitor = MediaDirectoryMonitor(service, config)
    monkeypatch.setattr(service, 'run_analysis', lambda *a, **k: pytest.fail('duplicate analyzed'))
    try:
        monitor._process_video(str(copy))
        monitor._dispatch_batch()
        items = service.media_registry.items()
        assert items[0]['stage'] == 'duplicate'
        assert monitor.snapshot()['overall_percent'] == 100
        assert not monitor.analysis_running
    finally:
        monitor.stop(shutdown_analysis=True)


def test_persistent_batch_resumes_and_runs_only_unfinished_stages(service, tmp_path, monkeypatch):
    root = tmp_path / 'watched'
    root.mkdir()
    path = root / 'clip.mp4'
    path.write_bytes(b'unique content')
    config = normalize_watch_config({'directories': [{'path': str(root), 'auto_rename': False, 'xmp_mode': 'off'}]})
    monitor = MediaDirectoryMonitor(service, config)
    monitor._process_video(str(path))
    row = service.media_registry.items()[0]
    service.media_registry.update_item(row['id'], 'analyzing')
    service.db.execute_non_query("UPDATE videos SET status='analyzed' WHERE path=?", (str(path),))
    monitor.stop(shutdown_analysis=True)
    restored = MediaDirectoryMonitor(service, config)
    calls = []
    def analyze(paths, **options):
        calls.append(paths)
        assert options['priority'] == 'background'
        for path in paths:
            service.db.execute_non_query("UPDATE videos SET status='analyzed' WHERE path=?", (path,))
        return {'succeeded': len(paths)}
    monkeypatch.setattr(service, 'run_analysis', analyze)
    try:
        restored._dispatch_batch()
        deadline = time.monotonic() + 3
        while restored.analysis_running and time.monotonic() < deadline:
            time.sleep(.01)
        assert calls == []
        assert service.media_registry.items()[0]['stage'] == 'done'
        restored._dispatch_batch()
        assert len(calls) == 0
    finally:
        restored.stop(shutdown_analysis=True)


def test_new_only_baseline_and_history_scan(service, tmp_path):
    root = tmp_path / 'watched'
    root.mkdir()
    old = root / 'old.mp4'
    old.write_bytes(b'old')
    config = normalize_watch_config({'initial_scan_mode': 'new', 'directories': [{'path': str(root)}]})
    monitor = MediaDirectoryMonitor(service, config)
    task = config['directories'][0]['id']
    try:
        monitor.scan_now()
        monitor._process_video(str(old))
        assert service.db.get_video_by_path(str(old)) is None
        new = root / 'new.mp4'
        new.write_bytes(b'new')
        monitor._process_video(str(new))
        assert service.db.get_video_by_path(str(new))
        monitor.scan_now(task, include_history=True)
        monitor._process_video(str(old))
        assert service.db.get_video_by_path(str(old))
    finally:
        monitor.stop(shutdown_analysis=True)


def test_manual_queue_has_priority_at_batch_boundary():
    queue = ProcessingQueue()
    order = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        with queue.claim('background'):
            background = pool.submit(lambda: _record_claim(queue, 'background', order))
            manual = pool.submit(lambda: _record_claim(queue, 'manual', order))
            deadline = time.monotonic() + 2
            while not queue._manual_waiters and time.monotonic() < deadline:
                time.sleep(.01)
        manual.result(timeout=3)
        background.result(timeout=3)
    assert order == ['manual', 'background']


def _record_claim(queue, priority, order):
    with queue.claim(priority):
        order.append(priority)


def test_config_inheritance_filters_and_stable_task_identity(tmp_path):
    config = normalize_watch_config({'batch_size': 20, 'exclude_patterns': ['*_proxy.mp4'],
        'directories': [{'path': str(tmp_path), 'auto_rename': True, 'name': '现场素材'}]})
    task = config['directories'][0]
    assert effective_directory_options(config, str(tmp_path / 'clip.mp4'))['batch_size'] == 20
    assert is_path_watchable(config, str(tmp_path / 'clip.mp4'))
    assert not is_path_watchable(config, str(tmp_path / 'clip_proxy.mp4'))
    identity = task['id']
    task['name'] = '改名后'
    assert normalize_watch_config(config)['directories'][0]['id'] == identity


def test_independent_page_and_shared_configuration(service, qt_app, monkeypatch):
    from gui.main_window import MainWindow
    monkeypatch.setattr('gui.views.media_watch_advanced.set_windows_startup', lambda _: None)
    window = MainWindow(service)
    try:
        window.nav_list.setCurrentRow(2)
        assert window.content_stack.currentWidget() is window.media_watch_page
        assert '素材监控' in window.nav_list.currentItem().text()
        window._open_watch_advanced('')
        assert window.content_stack.currentWidget() is window.settings_page
        assert window.settings_page.tabs.currentWidget() is window.settings_page.media_watch_page
        editor = window.settings_page.media_watch_page
        editor.fields['batch_size'].setValue(25)
        editor._save()
        assert service.settings['processing']['media_watch']['batch_size'] == 25
    finally:
        window._allow_exit = True
        window.close()
        window.deleteLater()
        qt_app.processEvents()


def wait_monitor(monitor):
    deadline = time.monotonic() + 5
    while monitor.analysis_running and time.monotonic() < deadline:
        time.sleep(.01)
    assert not monitor.analysis_running


def test_paused_task_retains_queue_and_frozen_configuration(service, tmp_path, monkeypatch):
    path = tmp_path / 'clip.mp4'
    path.write_bytes(b'unique')
    service.settings['processing']['max_workers'] = 2
    config = normalize_watch_config({'max_workers': 20, 'directories': [{'path': str(tmp_path)}]})
    task = config['directories'][0]['id']
    monitor = MediaDirectoryMonitor(service, config)
    calls = []
    def analyze(paths, **kwargs):
        calls.append(kwargs['execution_options'])
        for path in paths:
            service.db.execute_non_query("UPDATE videos SET status='analyzed' WHERE path=?", (path,))
    monkeypatch.setattr(service, 'run_analysis', analyze)
    try:
        monitor._process_video(str(path))
        monitor.pause_task(task)
        monitor._dispatch_batch()
        assert not calls
        service.settings['processing']['max_workers'] = 10
        monitor.pause_task(task, False)
        monitor._dispatch_batch()
        wait_monitor(monitor)
        assert calls[0]['max_workers'] == 2
        assert service.media_registry.items()[0]['stage'] == 'done'
    finally:
        monitor.stop(shutdown_analysis=True)


@pytest.mark.parametrize('result_saved', [False, True])
def test_pause_resume_does_not_repeat_dispatched_analysis(service, tmp_path, monkeypatch, result_saved):
    path = tmp_path / 'clip.mp4'
    path.write_bytes(b'one request must not be repeated')
    config = normalize_watch_config({'xmp_mode': 'off', 'directories': [{'path': str(tmp_path)}]})
    task = config['directories'][0]['id']
    monitor = MediaDirectoryMonitor(service, config)
    started = threading.Event()
    calls = []

    def analyze(paths, **kwargs):
        calls.append(list(paths))
        for started_path in paths:
            assert kwargs['callbacks']['item_started'](started_path)
        service.db.execute_non_query("UPDATE videos SET status='processing' WHERE path=?", (str(path),))
        started.set()
        if len(calls) == 1:
            assert kwargs['cancel_event'].wait(3)
        if result_saved:
            service.db.execute_non_query("UPDATE videos SET status='analyzed' WHERE path=?", (str(path),))
        return {'cancelled': True, 'attempted': 1, 'succeeded': int(result_saved)}

    monkeypatch.setattr(service, 'run_analysis', analyze)
    try:
        monitor._process_video(str(path))
        monitor._dispatch_batch()
        assert started.wait(3)
        monitor.pause_task(task)
        wait_monitor(monitor)
        item = service.media_registry.items()[0]
        assert item['stage'] == ('rename_ready' if result_saved else 'review')
        if not result_saved:
            assert item['error']
        monitor.pause_task(task, False)
        monitor._dispatch_batch()
        wait_monitor(monitor)
        assert calls == [[str(path)]]
        assert service.media_registry.items()[0]['stage'] == ('done' if result_saved else 'review')
    finally:
        monitor.stop(shutdown_analysis=True)


@pytest.mark.parametrize('resume_before_completion', [False, True])
def test_pause_keeps_undispatched_items_queued(service, tmp_path, monkeypatch, resume_before_completion):
    paths = [tmp_path / 'first.mp4', tmp_path / 'second.mp4']
    for index, path in enumerate(paths):
        path.write_bytes(f'unique video {index}'.encode())
    service.settings['processing']['max_workers'] = 1
    config = normalize_watch_config({'max_workers': 1, 'xmp_mode': 'off',
        'directories': [{'path': str(tmp_path)}]})
    task = config['directories'][0]['id']
    monitor = MediaDirectoryMonitor(service, config)
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def process(path, *args, **kwargs):
        calls.append(path)
        if path == str(paths[0]):
            entered.set()
            assert service._analysis_cancel.wait(3)
            assert release.wait(3)
            return None, '用户取消'
        return {**service.db.get_video_by_path(path), 'status': 'analyzed'}, None

    monkeypatch.setattr(service, '_process_single_video', process)
    try:
        for path in paths:
            monitor._process_video(str(path))
        monitor._dispatch_batch()
        assert entered.wait(3)
        monitor.pause_task(task)
        if resume_before_completion:
            monitor.pause_task(task, False)
        release.set()
        wait_monitor(monitor)
        stages = {item['path']: item['stage'] for item in service.media_registry.items()}
        assert stages[str(paths[0])] == 'review'
        assert stages[str(paths[1])] == 'queued'
        if not resume_before_completion:
            monitor.pause_task(task, False)
        monitor._dispatch_batch()
        wait_monitor(monitor)
        assert calls == [str(path) for path in paths]
        stages = {item['path']: item['stage'] for item in service.media_registry.items()}
        assert stages[str(paths[0])] == 'review'
        assert stages[str(paths[1])] == 'done'
    finally:
        release.set()
        monitor.stop(shutdown_analysis=True)


def test_quick_pause_resume_while_waiting_for_shared_queue(service, tmp_path, monkeypatch):
    path = tmp_path / 'clip.mp4'
    path.write_bytes(b'queued without any request')
    config = normalize_watch_config({'xmp_mode': 'off', 'directories': [{'path': str(tmp_path)}]})
    task = config['directories'][0]['id']
    monitor = MediaDirectoryMonitor(service, config)
    calls = []

    def analyze(paths, **kwargs):
        calls.extend(paths)
        for selected in paths:
            service.db.execute_non_query("UPDATE videos SET status='analyzed' WHERE path=?", (selected,))

    monkeypatch.setattr(service, 'run_analysis', analyze)
    try:
        monitor._process_video(str(path))
        with service.processing_queue.claim('manual'):
            monitor._dispatch_batch()
            assert monitor.analysis_running
            monitor.pause_task(task)
            monitor.pause_task(task, False)
        wait_monitor(monitor)
        assert not calls
        assert service.media_registry.items()[0]['stage'] == 'queued'
        assert service.db.execute_query('SELECT state FROM media_watch_batches')[0]['state'] == 'queued'
        monitor._dispatch_batch()
        wait_monitor(monitor)
        assert calls == [str(path)]
        assert service.media_registry.items()[0]['stage'] == 'done'
    finally:
        monitor.stop(shutdown_analysis=True)


def test_automatic_rename_has_one_session_and_self_events_keep_identity(service, tmp_path):
    config = normalize_watch_config({'auto_rename': True, 'rename_pattern': 'named-{original_name}',
        'directories': [{'path': str(tmp_path)}]})
    monitor = MediaDirectoryMonitor(service, config)
    originals = []
    try:
        for index in range(2):
            path = tmp_path / f'{index}.mp4'
            path.write_bytes(f'unique-{index}'.encode())
            row = service.media_registry.register(str(path))
            service.db.execute_non_query("UPDATE videos SET status='analyzed' WHERE id=?", (row['id'],))
            originals.append(str(path))
            monitor._process_video(str(path))
        monitor._dispatch_batch()
        wait_monitor(monitor)
        batches = service.db.execute_query('SELECT * FROM media_watch_batches')
        assert len(batches) == 1
        assert len(service.db.get_history_by_session(batches[0]['id'])) == 2
        for path in tmp_path.glob('named-*.mp4'):
            monitor._process_video(str(path))
        assert len(service.db.get_all_videos()) == 2
        assert service.rollback_last_session(batches[0]['id'])
        assert all(__import__('os').path.exists(path) for path in originals)
        assert {row['path'] for row in service.media_registry.items()} == set(originals)
    finally:
        monitor.stop(shutdown_analysis=True)


def test_xmp_retry_does_not_analyze_or_rename_again(service, tmp_path, monkeypatch):
    path = tmp_path / 'clip.mp4'
    path.write_bytes(b'one')
    row = service.media_registry.register(str(path))
    service.db.execute_non_query("UPDATE videos SET status='analyzed' WHERE id=?", (row['id'],))
    monitor = MediaDirectoryMonitor(service, normalize_watch_config({'xmp_mode': 'on', 'directories': [{'path': str(tmp_path)}]}))
    monkeypatch.setattr(service, 'run_analysis', lambda *a, **k: pytest.fail('already analyzed'))
    attempts = []
    monkeypatch.setattr(service, 'sync_metadata_to_xmp', lambda *a, **k: attempts.append(True) or {'failed_count': 1 if len(attempts) == 1 else 0})
    try:
        monitor._process_video(str(path))
        monitor._dispatch_batch()
        wait_monitor(monitor)
        item = service.media_registry.items()[0]
        assert item['stage'] == 'xmp_ready'
        monitor._dispatch_batch()
        assert len(attempts) == 1
        service.media_registry.reset_items([item['id']], 'xmp_ready')
        monitor._dispatch_batch()
        wait_monitor(monitor)
        assert len(attempts) == 2
        assert service.media_registry.items()[0]['stage'] == 'done'
    finally:
        monitor.stop(shutdown_analysis=True)


def test_restart_does_not_repeat_uncertain_analysis_request(service, tmp_path):
    path = tmp_path / 'clip.mp4'
    path.write_bytes(b'one')
    config = normalize_watch_config({'directories': [{'path': str(tmp_path)}]})
    first = MediaDirectoryMonitor(service, config)
    first._process_video(str(path))
    service.media_registry.update_item(service.media_registry.items()[0]['id'], 'analyzing')
    first.stop(shutdown_analysis=True)
    restored = MediaDirectoryMonitor(service, config)
    try:
        assert service.media_registry.items()[0]['stage'] == 'review'
        restored._dispatch_batch()
        assert not restored.analysis_running
    finally:
        restored.stop(shutdown_analysis=True)


def test_owned_metadata_injection_continues_monitor_rename_and_xmp(service, tmp_path, monkeypatch):
    path = tmp_path / 'clip.mp4'
    path.write_bytes(b'video before owned metadata injection')
    service.settings['processing']['enable_metadata_injection'] = True
    config = normalize_watch_config({'auto_rename': True, 'rename_pattern': 'named-{original_name}',
        'xmp_mode': 'on', 'directories': [{'path': str(tmp_path)}]})
    monitor = MediaDirectoryMonitor(service, config)
    injected, synced = [], []

    def inject(video_path, tags, summary):
        injected.append(video_path)
        with open(video_path, 'ab') as stream:
            stream.write(b' owned metadata')
        return True

    monkeypatch.setattr(service.processor, 'extract_frames', lambda *a, **k: {'frames': ['fake']})
    monkeypatch.setattr(service.ai, 'analyze_video',
        lambda *a, **k: {'category': 'Broll', 'tags': ['test'], 'summary': 'saved analysis'})
    monkeypatch.setattr('core.video_organizer_service.MetadataInjector.inject_tags', inject)
    monkeypatch.setattr(service, 'sync_metadata_to_xmp',
        lambda paths, **kwargs: synced.extend(paths) or {'failed_count': 0})
    try:
        monitor._process_video(str(path))
        monitor._dispatch_batch()
        wait_monitor(monitor)
        item = service.media_registry.items()[0]
        current = service.db.execute_query('SELECT * FROM videos WHERE id=?', (item['video_id'],))[0]
        assert item['stage'] == 'done'
        assert item['content_version'] == current['content_version']
        assert current['filename'] == 'named-clip.mp4'
        assert injected == [str(path)]
        assert synced == [current['path']]
    finally:
        monitor.stop(shutdown_analysis=True)


@pytest.mark.parametrize('entrypoint', ['stable_pending', 'registration'])
def test_changed_exclusion_applies_before_pending_file_registration(service, tmp_path, monkeypatch, entrypoint):
    path = tmp_path / 'excluded.mp4'
    path.write_bytes(b'file discovered before filter changed')
    config = normalize_watch_config({'stability_seconds': 0, 'auto_analyze': False,
        'directories': [{'path': str(tmp_path)}]})
    monitor = MediaDirectoryMonitor(service, config)
    monkeypatch.setattr(monitor, 'start', lambda: False)
    try:
        monitor.notify_path(str(path))
        assert monitor._pending
        updated = {**config, 'exclude_patterns': ['*.mp4']}
        monitor.reconfigure(updated)
        assert not is_path_watchable(monitor.config, str(path))
        if entrypoint == 'stable_pending':
            monitor._process_stable_files(time.monotonic() + 1)
        else:
            monitor._process_video(str(path))
        assert service.db.get_video_by_path(str(path)) is None
        assert not service.media_registry.items()
        if entrypoint == 'stable_pending':
            assert not monitor._pending
    finally:
        monitor.stop(shutdown_analysis=True)


def test_replaced_content_during_analysis_never_overwrites_new_version(service, tmp_path, monkeypatch):
    path = tmp_path / 'clip.mp4'
    path.write_bytes(b'initial')
    service.media_registry.register(str(path))
    monkeypatch.setattr(service.processor, 'extract_frames', lambda *a, **k: {'frames': ['fake']})
    def analyze(*a, **k):
        path.write_bytes(b'replaced while request in flight')
        return {'category': 'Broll', 'tags': ['stale'], 'summary': 'stale result'}
    monkeypatch.setattr(service.ai, 'analyze_video', analyze)
    result = service.run_analysis([str(path)])
    row = service.db.get_video_by_path(str(path))
    assert not result['succeeded']
    assert row['content_version'] == 2
    assert row['fingerprint_state'] == 'review'
    assert row['tags'] == [] and row['summary'] != 'stale result'


def test_running_batch_settings_do_not_overwrite_gui_changes(service, tmp_path, monkeypatch):
    path = tmp_path / 'clip.mp4'
    path.write_bytes(b'one')
    started, release = threading.Event(), threading.Event()
    original = service.settings
    def run(*args, **kwargs):
        assert service.settings['processing']['max_workers'] == 1
        started.set()
        assert release.wait(3)
        assert service.settings['processing']['max_workers'] == 1
        return {'succeeded': 1}
    monkeypatch.setattr(service, '_run_analysis', run)
    with ThreadPoolExecutor(max_workers=1) as pool:
        job = pool.submit(service.run_analysis, [str(path)], execution_options={'max_workers': 1})
        assert started.wait(3)
        assert service.settings is original
        service.settings['processing']['max_workers'] = 7
        release.set()
        job.result(timeout=3)
    assert service.settings['processing']['max_workers'] == 7


def test_take_over_uses_selection_from_operation_start(service, tmp_path, monkeypatch):
    from types import SimpleNamespace
    from gui.views.media_watch_workspace import MediaWatchWorkspace

    monitor = MediaDirectoryMonitor(service, normalize_watch_config({'auto_analyze': False,
        'directories': [{'path': str(tmp_path)}]}))
    captured, appended = {}, []
    try:
        for index in range(2):
            path = tmp_path / f'clip-{index}.mp4'
            path.write_bytes(f'unique {index}'.encode())
            monitor._process_video(str(path))
        rows = sorted(service.media_registry.items(), key=lambda row: row['id'])
        selection = [rows[0]]
        signal = SimpleNamespace(emit=lambda *args: None)
        page = SimpleNamespace(service=service, registry=service.media_registry,
            selected_items=lambda: list(selection), work_scope_changed=signal, status_message=signal)

        def job(fn, on_ok):
            page._operation_rows = [dict(row) for row in selection]
            captured['paths'] = fn()
            captured['complete'] = on_ok

        page._job = job
        page._paths = lambda: MediaWatchWorkspace._paths(page)
        monkeypatch.setattr(service, 'append_work_scope',
            lambda paths, scan=False: appended.extend(paths))
        MediaWatchWorkspace.take_over(page)
        selection[:] = [rows[1]]
        captured['complete'](captured['paths'])
        stages = {row['id']: row['stage'] for row in service.media_registry.items()}
        assert appended == [rows[0]['path']]
        assert stages[rows[0]['id']] == 'manual'
        assert stages[rows[1]['id']] == 'pending'
    finally:
        monitor.stop(shutdown_analysis=True)


def test_accept_changes_uses_selection_from_operation_start(service, tmp_path, monkeypatch):
    from types import SimpleNamespace
    from PySide6.QtWidgets import QMessageBox
    from gui.views.media_watch_workspace import MediaWatchWorkspace

    monitor = MediaDirectoryMonitor(service, normalize_watch_config({'auto_analyze': False,
        'directories': [{'path': str(tmp_path)}]}))
    captured = {}
    try:
        for index in range(2):
            path = tmp_path / f'clip-{index}.mp4'
            path.write_bytes(f'unique {index}'.encode())
            monitor._process_video(str(path))
        rows = sorted(service.media_registry.items(), key=lambda row: row['id'])
        service.media_registry.update_item(rows[0]['id'], 'review')
        service.db.execute_non_query("UPDATE videos SET fingerprint_state='review' WHERE id=?", (rows[0]['video_id'],))
        rows[0]['stage'] = 'review'
        selection = [rows[0]]
        page = SimpleNamespace(registry=service.media_registry, monitor=monitor,
            selected_items=lambda: list(selection), refresh=lambda: None)
        page.set_items = lambda stage: MediaWatchWorkspace.set_items(page, stage)

        def job(fn, on_ok):
            captured['fn'] = fn
            captured['complete'] = on_ok

        page._job = job
        monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.Yes)
        MediaWatchWorkspace.accept_changes(page)
        selection[:] = [rows[1]]
        captured['complete'](captured['fn']())
        stages = {row['id']: row['stage'] for row in service.media_registry.items()}
        assert service.db.get_video_by_path(rows[0]['path'])['fingerprint_state'] == 'verified'
        assert stages[rows[0]['id']] == 'queued'
        assert stages[rows[1]['id']] == 'pending'
    finally:
        monitor.stop(shutdown_analysis=True)


def test_unreadable_item_does_not_block_other_items_and_recovers(service, tmp_path, monkeypatch):
    paths = [tmp_path / 'locked.mp4', tmp_path / 'readable.mp4']
    monitor = MediaDirectoryMonitor(service, normalize_watch_config({'xmp_mode': 'off', 'stability_seconds': 0,
        'initial_scan_mode': 'new',
        'directories': [{'path': str(tmp_path)}]}))
    calls = []
    blocked = [True]
    original_hasher = service.media_registry.hasher

    def hasher(path):
        if path == str(paths[0]) and blocked[0]:
            raise PermissionError('temporary file sharing violation')
        return original_hasher(path)

    def analyze(selected, **kwargs):
        calls.append(list(selected))
        for path in selected:
            kwargs['callbacks']['item_started'](path)
            service.db.execute_non_query("UPDATE videos SET status='analyzed' WHERE path=?", (path,))

    monkeypatch.setattr(service, 'run_analysis', analyze)
    try:
        for index, path in enumerate(paths):
            path.write_bytes(f'unique {index}'.encode())
            monitor._process_video(str(path))
        monkeypatch.setattr(service.media_registry, 'hasher', hasher)
        monitor._dispatch_batch()
        wait_monitor(monitor)
        assert calls == [[str(paths[1])]]
        items = {row['path']: row for row in service.media_registry.items()}
        assert items[str(paths[0])]['stage'] == 'queued'
        assert items[str(paths[0])]['error']
        assert items[str(paths[1])]['stage'] == 'done'
        assert service.db.execute_query('SELECT state FROM media_watch_batches')[0]['state'] == 'paused'
        blocked[0] = False
        monitor._process_video(str(paths[0]))
        assert service.db.execute_query('SELECT state FROM media_watch_batches')[0]['state'] == 'queued'
        monitor._dispatch_batch()
        wait_monitor(monitor)
        assert calls == [[str(paths[1])], [str(paths[0])]]
        assert all(row['stage'] == 'done' for row in service.media_registry.items())
    finally:
        monitor.stop(shutdown_analysis=True)


def test_unreadable_item_recovers_from_periodic_scan(service, tmp_path, monkeypatch):
    from core import media_watch

    path = tmp_path / 'locked.mp4'
    path.write_bytes(b'unique content')
    monitor = MediaDirectoryMonitor(service, normalize_watch_config({'xmp_mode': 'off', 'stability_seconds': 0,
        'directories': [{'path': str(tmp_path)}]}))
    calls = []
    blocked = [True]
    original_hasher = service.media_registry.hasher

    def hasher(target):
        if target == str(path) and blocked[0]:
            raise PermissionError('temporary file sharing violation')
        return original_hasher(target)

    monkeypatch.setattr(media_watch, 'STABILITY_SECONDS', 0)

    def analyze(selected, **kwargs):
        calls.extend(selected)
        for target in selected:
            kwargs['callbacks']['item_started'](target)
            service.db.execute_non_query("UPDATE videos SET status='analyzed' WHERE path=?", (target,))

    monkeypatch.setattr(service, 'run_analysis', analyze)
    try:
        monitor._process_video(str(path))
        monitor.scan_now()
        monkeypatch.setattr(service.media_registry, 'hasher', hasher)
        monitor._dispatch_batch()
        wait_monitor(monitor)
        item = service.media_registry.items()[0]
        assert item['stage'] == 'queued'
        assert item['error'].startswith('等待文件可读取：')
        assert service.db.execute_query('SELECT state FROM media_watch_batches')[0]['state'] == 'paused'

        blocked[0] = False
        monitor.scan_now()
        assert monitor._pending, (monitor.statuses(), service.media_registry.items())
        monitor._process_stable_files(time.monotonic() + 1)
        assert service.media_registry.items()[0]['error'] == '', service.media_registry.items()[0]
        assert service.db.execute_query('SELECT state FROM media_watch_batches')[0]['state'] == 'queued', service.db.execute_query('SELECT state FROM media_watch_batches')
        assert not monitor.analysis_running
        monitor._dispatch_batch()
        wait_monitor(monitor)

        item = service.media_registry.items()[0]
        assert calls == [str(path)]
        assert item['stage'] == 'done'
        assert item['error'] == ''
        assert service.db.execute_query('SELECT state FROM media_watch_batches')[0]['state'] == 'completed'
    finally:
        monitor.stop(shutdown_analysis=True)


def test_rejected_job_does_not_replace_running_job_selection(service, tmp_path, monkeypatch):
    from types import SimpleNamespace
    from gui.views import media_watch_workspace as workspace

    monitor = MediaDirectoryMonitor(service, normalize_watch_config({'auto_analyze': False,
        'directories': [{'path': str(tmp_path)}]}))
    captured, appended = {}, []

    def run_job(parent, *, fn, on_ok, on_start=None, **kwargs):
        if captured:
            return None
        if on_start:
            on_start()
        captured.update(fn=fn, complete=on_ok)

    monkeypatch.setattr(workspace, 'run_io_job', run_job)
    monkeypatch.setattr(service, 'append_work_scope', lambda paths, scan=False: appended.extend(paths))
    try:
        for index in range(2):
            path = tmp_path / f'clip-{index}.mp4'
            path.write_bytes(f'unique {index}'.encode())
            monitor._process_video(str(path))
        rows = sorted(service.media_registry.items(), key=lambda row: row['id'])
        selection = [rows[0]]
        signal = SimpleNamespace(emit=lambda *args: None)
        page = SimpleNamespace(service=service, registry=service.media_registry,
            selected_items=lambda: list(selection), refresh=lambda: None,
            work_scope_changed=signal, status_message=signal)
        page._paths = lambda: workspace.MediaWatchWorkspace._paths(page)
        page._job = lambda fn, on_ok=None: workspace.MediaWatchWorkspace._job(page, fn, on_ok)
        workspace.MediaWatchWorkspace.take_over(page)
        selection[:] = [rows[1]]
        page._job(lambda: None)
        captured['complete'](captured['fn']())
        stages = {row['id']: row['stage'] for row in service.media_registry.items()}
        assert appended == [rows[0]['path']]
        assert stages[rows[0]['id']] == 'manual'
        assert stages[rows[1]['id']] == 'pending'
    finally:
        monitor.stop(shutdown_analysis=True)


@pytest.mark.parametrize('decision', ['manual', 'ignored'])
def test_waiting_batch_keeps_later_human_decision(service, tmp_path, monkeypatch, decision):
    path = tmp_path / 'clip.mp4'
    path.write_bytes(b'queued before human decision')
    config = normalize_watch_config({'directories': [{'path': str(tmp_path)}]})
    task = config['directories'][0]['id']
    monitor = MediaDirectoryMonitor(service, config)
    monkeypatch.setattr(service, 'run_analysis', lambda *a, **k: pytest.fail('human decision was overwritten'))
    try:
        monitor._process_video(str(path))
        item = service.media_registry.items()[0]
        with service.processing_queue.claim('manual'):
            monitor._dispatch_batch()
            service.media_registry.reset_items([item['id']], decision)
            monitor.pause_task(task)
            monitor.pause_task(task, False)
        wait_monitor(monitor)
        assert service.media_registry.items()[0]['stage'] == decision
        monitor._dispatch_batch()
        wait_monitor(monitor)
        assert service.media_registry.items()[0]['stage'] == decision
    finally:
        monitor.stop(shutdown_analysis=True)


def test_duplicate_thumbnail_never_decodes_video(service, qt_app, tmp_path, monkeypatch):
    from PySide6.QtCore import Qt
    from gui.models.video_table import VideoTableModel, COL_THUMB
    path = tmp_path / 'copy.mp4'
    path.write_bytes(b'duplicate')
    model = VideoTableModel(thumbnail_dir=str(tmp_path))
    model.update_data([{'path': str(path), 'duplicate_of': 1}])
    monkeypatch.setattr('core.video_organizer_service.VideoProcessor.extract_frames', lambda *a, **k: pytest.fail('duplicate thumbnail decoded'))
    model.data(model.index(0, COL_THUMB), Qt.DecorationRole)
    assert model.thumbnail_pool.waitForDone(3000)
    qt_app.processEvents()


def test_manual_task_waits_until_background_rename_finishes(service, tmp_path, monkeypatch):
    path = tmp_path / 'clip.mp4'
    path.write_bytes(b'one')
    row = service.media_registry.register(str(path))
    service.db.execute_non_query("UPDATE videos SET status='analyzed' WHERE id=?", (row['id'],))
    monitor = MediaDirectoryMonitor(service, normalize_watch_config({'auto_rename': True,
        'directories': [{'path': str(tmp_path)}]}))
    entered, release, manual_started = threading.Event(), threading.Event(), threading.Event()
    def rename(*args):
        entered.set()
        assert release.wait(3)
        return True
    monkeypatch.setattr(monitor, '_rename_item', rename)
    monkeypatch.setattr(service, '_run_analysis', lambda *a, **k: manual_started.set() or {'succeeded': 1})
    try:
        monitor._process_video(str(path))
        monitor._dispatch_batch()
        assert entered.wait(3)
        with ThreadPoolExecutor(max_workers=1) as pool:
            job = pool.submit(service.run_analysis, [str(path)], priority='manual')
            try:
                deadline = time.monotonic() + 2
                while not service.processing_queue._manual_waiters and time.monotonic() < deadline:
                    time.sleep(.01)
                assert service.processing_queue._manual_waiters == 1
                assert not manual_started.is_set()
            finally:
                release.set()
            job.result(timeout=3)
        assert manual_started.is_set()
        wait_monitor(monitor)
        assert service.media_registry.items()[0]['stage'] == 'done'
    finally:
        release.set()
        monitor.stop(shutdown_analysis=True)


def test_excluded_readable_video_is_not_marked_missing(service, tmp_path):
    path = tmp_path / 'clip.mp4'
    path.write_bytes(b'one')
    service.media_registry.register(str(path))
    monitor = MediaDirectoryMonitor(service, normalize_watch_config({'exclude_patterns': ['*.mp4'],
        'directories': [{'path': str(tmp_path)}]}))
    try:
        monitor.scan_now()
        assert not service.db.get_video_by_path(str(path))['is_missing']
        assert not service.media_registry.items()
    finally:
        monitor.stop(shutdown_analysis=True)
