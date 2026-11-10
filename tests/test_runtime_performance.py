"""复现启动探测、32任务连接初始化与UI全量摘要路径。"""
import json
import subprocess
import sys
import threading
import time
import os
from concurrent.futures import ThreadPoolExecutor

from core.ai_gateway import AiRequestGateway
import pytest


@pytest.fixture
def service(tmp_path):
    from core.video_organizer_service import VideoOrganizerService, SettingsManager
    return VideoOrganizerService(settings=SettingsManager.deep_copy_defaults(), db_path=str(tmp_path/'test.db'), on_log=lambda *_: None)


def test_startup_import_does_not_launch_optional_tools():
    script = '''
import json, sys
events=[]
sys.addaudithook(lambda event,args: events.append(str(args[1])) if event=='subprocess.Popen' else None)
import video_organizer_app
print(json.dumps(events))
'''
    result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, check=True, timeout=30)
    assert json.loads(result.stdout.splitlines()[-1]) == []


def test_first_32_tasks_share_one_route_client(monkeypatch):
    gateway = AiRequestGateway({})
    route = {'provider_id': 'local', 'api_key': 'test', 'base_url': 'http://localhost', 'model': 'test'}
    monkeypatch.setattr(gateway, 'resolve_route', lambda _: dict(route))
    barrier = threading.Barrier(32)
    created = []
    def make(*_):
        client = object()
        created.append(client)
        time.sleep(0.03)
        return client
    monkeypatch.setattr(gateway, '_client_for_route', make)
    def task(_):
        barrier.wait(timeout=10)
        return gateway.client_for_task('video_classification')[0]
    with ThreadPoolExecutor(max_workers=32) as executor:
        clients = list(executor.map(task, range(32)))
    assert len(created) == 1
    assert all(client is clients[0] for client in clients)


def test_all_parallel_settings_save_and_reload_512(app, service):
    from gui.views.settings import SettingsView
    view = SettingsView(service)
    for widget in (view.workers_spin, view.extract_parallel_spin, view.extract_workers_spin):
        assert widget.maximum() == 512
        widget.setValue(512)
    assert view.apply_settings(silent=True)
    with open(service._settings_path, encoding='utf-8') as handle:
        persisted = json.load(handle)
    for key in ('max_workers', 'extract_parallel', 'extract_workers'):
        assert persisted['processing'][key] == 512
    view.close()
    reopened = SettingsView(service)
    assert [w.value() for w in (reopened.workers_spin, reopened.extract_parallel_spin, reopened.extract_workers_spin)] == [512, 512, 512]
    reopened.close()


@pytest.mark.parametrize('configured', [512, 1024])
def test_analysis_runtime_honors_parallel_limit_512(tmp_path, monkeypatch, configured):
    from core.video_organizer_service import SettingsManager, VideoOrganizerService
    settings = SettingsManager.deep_copy_defaults()
    settings['processing'].update(max_workers=configured, extract_parallel=configured, auto_sync_xmp=False)
    svc = VideoOrganizerService(settings=settings, db_path=str(tmp_path / 'parallel.db'), on_log=lambda *_: None)
    assert svc._extract_parallel == 512
    path = tmp_path / 'one.mp4'
    path.write_bytes(b'synthetic')
    svc.db.insert_videos_if_absent([{'path': str(path), 'filename': path.name, 'status': 'pending'}])
    workers = []
    def executor(*a, **kw):
        workers.append(kw['max_workers'])
        return ThreadPoolExecutor(*a, **kw)
    monkeypatch.setattr('core.video_organizer_service.ThreadPoolExecutor', executor)
    monkeypatch.setattr(svc.processor, 'extract_frames', lambda *_a, **_kw: {'frames': ['synthetic'], 'thumbnail': None, 'phash': ''})
    monkeypatch.setattr(svc.ai, 'analyze_video', lambda *_a, **_kw: {'category': 'Broll', 'summary': 'ok', 'tags': [], 'tag_groups': {}})
    monkeypatch.setattr(svc.ai, 'apply_tag_normalization', lambda data, **_: data)
    monkeypatch.setattr(svc.tag_processor, 'process', lambda tags, category: (tags, category))
    outcome = svc.run_analysis([str(path)], force_reanalyze=True)
    assert outcome['succeeded'] == 1
    assert workers == [512]
    assert svc._extract_parallel == 512


@pytest.mark.parametrize('view_name', ['MaterialLibraryView', 'WorkstationView'])
def test_summary_refresh_does_not_expand_catalog_paths(app, service, monkeypatch, view_name):
    from gui.views.material_library import MaterialLibraryView
    from gui.views.workstation import WorkstationView
    view = {'MaterialLibraryView': MaterialLibraryView, 'WorkstationView': WorkstationView}[view_name](service)
    calls = []
    monkeypatch.setattr(view, '_get_visible_paths', lambda: calls.append('full scan') or [])
    for _ in range(5):
        view._refresh_operation_target_summary()
    assert not calls
    view._query_debounce.stop()
    view.close()


def test_import_batches_preserve_properties_and_aliases(service, monkeypatch):
    service.db.add_tag('mood', 'existing', color='#123456', name_en='existing-en')
    next(g for g in service.tag_config['tag_groups'] if g['id'] == 'mood')['tags'] = [{'name':'existing','en':'existing-en','icon':'star'}]
    calls = []
    original = service.db._get_connection
    def connect():
        conn = original()
        conn.set_trace_callback(lambda sql: calls.append(sql) if sql == 'COMMIT' else None)
        return conn
    monkeypatch.setattr(service.db, '_get_connection', connect)
    service.commit_tag_import({'subject': ['existing'] + [f'tag{i}' for i in range(500)]}, {'alias': 'tag0'})
    details = {row['tag_name']: row for row in service.db.get_tags_detail()}
    assert details['existing']['dimension'] == 'subject'
    assert details['existing']['color'] == '#123456'
    assert details['existing']['name_en'] == 'existing-en'
    assert service.db.get_synonyms()['alias'] == 'tag0'
    assert len(details) == 501
    assert len(calls) < 10, 'import must not commit per tag'
    assert 'existing' not in next(g['tags'] for g in service.tag_config['tag_groups'] if g['id'] == 'mood')
    assert {'name':'existing','en':'existing-en','icon':'star'} in next(g['tags'] for g in service.tag_config['tag_groups'] if g['id'] == 'subject')


def test_empty_preview_does_not_initialize_media_devices(app, monkeypatch):
    import gui.widgets.media_preview as module
    created = []
    monkeypatch.setattr(module, 'QMediaPlayer', lambda *a, **k: created.append('player'))
    monkeypatch.setattr(module, 'QAudioOutput', lambda *a, **k: created.append('audio'))
    widget = module.MediaPreviewWidget()
    assert not created
    widget.set_media(None)
    assert not created
    widget.close()


@pytest.mark.skipif(os.name != 'nt', reason='Windows进程窗口标志')
def test_optional_ffmpeg_probe_is_hidden_without_affecting_other_processes(monkeypatch):
    from core.background_process import quiet_ffmpeg_probe
    calls = []
    def original(args, **kwargs):
        calls.append((args, kwargs))
    monkeypatch.setattr(subprocess, 'Popen', original)
    with quiet_ffmpeg_probe():
        subprocess.Popen(['ffmpeg', '-v', 'quiet'])
        subprocess.Popen(['setup.exe'])
        subprocess.Popen(['ffmpeg'], creationflags=subprocess.CREATE_NEW_CONSOLE)
    assert subprocess.Popen is original
    assert calls[0][1]['creationflags'] & subprocess.CREATE_NO_WINDOW
    assert not calls[1][1]
    assert calls[2][1]['creationflags'] == subprocess.CREATE_NEW_CONSOLE


def test_tag_import_transaction_failure_rolls_back_and_reports_backup(service):
    service.db.add_tag('mood', 'existing')
    service.db.execute_non_query("CREATE TRIGGER fail_import BEFORE INSERT ON tags_library WHEN NEW.tag_name='fail' BEGIN SELECT RAISE(ABORT, 'injected failure'); END")
    with pytest.raises(RuntimeError, match='已回滚.*备份保留'):
        service.commit_tag_import({'subject': ['existing', 'fail']})
    assert service.db.get_tags_detail()[0]['dimension'] == 'mood'
    assert len(service.db.get_tags_detail()) == 1
    assert service.db.get_storage_metadata('tag_import_projection_pending') is None


def test_tag_import_file_failure_keeps_authoritative_db_and_backup(service, monkeypatch):
    def fail(*_):
        raise OSError('injected full disk')
    # Backup uses its own atomic writer, so this fails only the final projection.
    monkeypatch.setattr('core.video_organizer_service.write_json_atomic', fail)
    with pytest.raises(RuntimeError, match='已写入数据库.*配置投影保存失败.*保留备份'):
        service.commit_tag_import({'subject': ['persisted']})
    assert service.db.get_tags_detail()[0]['tag_name'] == 'persisted'


@pytest.mark.parametrize('restart_write_fails', [False, True])
def test_import_projection_failure_never_reimports_old_group(service, monkeypatch, restart_write_fails):
    from core.video_organizer_service import VideoOrganizerService
    service.db.add_tag('mood', 'moved')
    next(g for g in service.tag_config['tag_groups'] if g['id'] == 'mood')['tags'] = [
        {'name': 'moved', 'en': 'moved-en', 'icon': 'star'},
    ]
    service.save_tag_config(service.tag_config)
    original = __import__('core.video_organizer_service', fromlist=['write_json_atomic']).write_json_atomic
    def fail(*_):
        raise OSError('injected projection failure')
    monkeypatch.setattr('core.video_organizer_service.write_json_atomic', fail)
    with pytest.raises(RuntimeError, match='配置投影保存失败'):
        service.commit_tag_import({'subject': ['moved']})
    assert service.db.get_storage_metadata('tag_import_projection_pending') is not None
    # 同进程重载不再把旧组回灌。
    service.heal_tag_config_tags_from_db()
    service.ai.init_tag_libraries()
    assert [(r['dimension'], r['tag_name']) for r in service.db.get_tags_detail()] == [('subject', 'moved')]
    if not restart_write_fails:
        monkeypatch.setattr('core.video_organizer_service.write_json_atomic', original)
    restarted = VideoOrganizerService(settings=service.settings, db_path=service._database_path, on_log=lambda *_: None)
    assert [(r['dimension'], r['tag_name']) for r in restarted.db.get_tags_detail()] == [('subject', 'moved')]
    assert next(g['tags'] for g in restarted.tag_config['tag_groups'] if g['id'] == 'mood') == []
    assert {'name': 'moved', 'en': 'moved-en', 'icon': 'star'} in next(g['tags'] for g in restarted.tag_config['tag_groups'] if g['id'] == 'subject')
    assert bool(restarted.db.get_storage_metadata('tag_import_projection_pending')) == restart_write_fails


def test_import_recovers_if_process_stops_after_database_commit(service):
    from core.video_organizer_service import VideoOrganizerService
    service.db.add_tag('mood', 'moved')
    next(g for g in service.tag_config['tag_groups'] if g['id'] == 'mood')['tags'] = ['moved']
    service.save_tag_config(service.tag_config)
    # 直接完成同一DB事务，模拟进程未执行任何内存替换/文件写入即退出。
    service.db.import_tags_batch({'moved': 'subject'}, {}, projection_recovery={'config': service.tag_config, 'backup': 'test-only'})
    restarted = VideoOrganizerService(settings=service.settings, db_path=service._database_path, on_log=lambda *_: None)
    assert [(r['dimension'], r['tag_name']) for r in restarted.db.get_tags_detail()] == [('subject', 'moved')]
    assert restarted.db.get_storage_metadata('tag_import_projection_pending') is None


@pytest.mark.parametrize('save_write_fails', [False, True])
def test_pending_projection_keeps_later_group_rule_changes(service, monkeypatch, save_write_fails):
    from core.video_organizer_service import VideoOrganizerService
    import core.video_organizer_service as module
    original = module.write_json_atomic
    def fail(*_):
        raise OSError('injected projection failure')
    monkeypatch.setattr(module, 'write_json_atomic', fail)
    with pytest.raises(RuntimeError):
        service.commit_tag_import({'subject': ['new']})
    subject = next(g for g in service.tag_config['tag_groups'] if g['id'] == 'subject')
    subject['rules']['local_prompt'] = 'latest user rule'
    if not save_write_fails:
        monkeypatch.setattr(module, 'write_json_atomic', original)
    service.save_tag_config(service.tag_config)
    assert bool(service.db.get_storage_metadata('tag_import_projection_pending')) == save_write_fails
    monkeypatch.setattr(module, 'write_json_atomic', original)
    restarted = VideoOrganizerService(settings=service.settings, db_path=service._database_path, on_log=lambda *_: None)
    assert next(g for g in restarted.tag_config['tag_groups'] if g['id'] == 'subject')['rules']['local_prompt'] == 'latest user rule'
    assert [(r['dimension'], r['tag_name']) for r in restarted.db.get_tags_detail()] == [('subject', 'new')]


@pytest.mark.parametrize('classified,aliases', [({'pool':['bad']}, {}), ({'unknown':['bad']}, {}), ({'subject':['same'],'mood':['same']}, {}), ({'subject':['ok']}, {'alias':'absent'})])
def test_invalid_import_is_rejected_before_writes(service, classified, aliases):
    with pytest.raises(ValueError):
        service.commit_tag_import(classified, aliases)
    assert service.db.get_tags_detail() == []


def test_import_backup_failure_prevents_writes(service, monkeypatch):
    from core.storage_backup import StorageBackupManager
    def fail(_):
        raise OSError('backup disk full')
    monkeypatch.setattr(StorageBackupManager, 'create_backup', fail)
    with pytest.raises(OSError, match='backup disk full'):
        service.commit_tag_import({'subject': ['new']})
    assert not service.db.get_tags_detail()


@pytest.mark.parametrize('view_name', ['MaterialLibraryView', 'WorkstationView'])
def test_lightweight_summary_keeps_full_query_operation_scope(app, service, tmp_path, view_name):
    from gui.views.material_library import MaterialLibraryView
    from gui.views.workstation import WorkstationView
    paths = [str(tmp_path/f'{i}.mp4') for i in range(7)]
    service.db.insert_videos_if_absent([{'path': p, 'filename': p, 'status': 'pending'} for p in paths])
    service.set_work_scope_paths([str(tmp_path)], scan=False)
    cls = {'MaterialLibraryView': MaterialLibraryView, 'WorkstationView': WorkstationView}[view_name]
    view = cls(service)
    view._page_total = 7
    view.model.update_data(service.query_video_page({}, limit=2).rows)
    view._refresh_operation_target_summary()
    assert '7 条' in view.target_summary_label.text()
    assert set(view._resolve_batch_targets()) == set(paths)
    view._query_debounce.stop()
    view.close()


@pytest.mark.parametrize('fail_import', [False, True])
def test_import_commit_runs_off_gui_thread_and_reports_result(app, service, monkeypatch, fail_import):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QWizard
    from gui.views.tags_library import TagsView
    view = TagsView(service)
    class Wizard:
        final_classified = {'subject': ['one']}
        imported_aliases = {}
        def __init__(self, *_):
            pass
        def exec(self):
            return QWizard.Accepted
    monkeypatch.setattr('gui.views.tags_library.TagImportWizard', Wizard)
    finished = threading.Event()
    threads = []
    def commit(*_):
        threads.append(threading.get_ident())
        assert finished.wait(3), 'UI event loop was blocked during import'
        if fail_import:
            raise OSError('injected failure')
        return {'imported': 1}
    monkeypatch.setattr(service, 'commit_tag_import', commit)
    warnings, successes = [], []
    monkeypatch.setattr('gui.views.tags_library.QMessageBox.warning', lambda *_: warnings.append(True))
    monkeypatch.setattr('gui.views.tags_library.QMessageBox.information', lambda *_: successes.append(True))
    QTimer.singleShot(30, finished.set)
    view.import_from_txt()
    assert threads and threads[0] != threading.get_ident()
    assert bool(warnings) == fail_import
    assert bool(successes) != fail_import
    worker = getattr(view, '_tag_import_worker', None)
    if worker:
        worker.wait(5000)
    app.processEvents()
    view.close()
