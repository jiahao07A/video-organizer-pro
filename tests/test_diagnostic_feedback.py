"""诊断反馈的隐私、容量、跨线程关联和离线GUI回归。"""
import json
import logging
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import zipfile

import pytest

from core import diagnostic_logging as dl
from core.diagnostic_bundle import build_bundle, BundleCancelled, issue_url


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    old = dl.get_runtime()
    if old and not old.closed:
        old.flush()
    monkeypatch.setattr(dl, '_runtime', None)
    logger = logging.getLogger('VideoOrganizer')
    handlers, level, propagate = list(logger.handlers), logger.level, logger.propagate
    logger.handlers = []
    runtime = dl.initialize(tmp_path / 'logs')
    yield runtime
    runtime.close()
    logger.handlers = handlers
    logger.setLevel(level)
    logger.propagate = propagate
    monkeypatch.setattr(dl, '_runtime', old)


def read_events(runtime):
    runtime.flush()
    return [json.loads(line) for path in runtime.files() if path.name.startswith('events-') for line in path.read_text(encoding='utf-8').splitlines()]


def test_redaction_before_disk_and_bundle(runtime, tmp_path):
    secret = 'synthetic-secret-ONLY-123'
    dl.register_settings({'model_providers': [{'api_key': secret}]})
    filename = '客户访谈.mp4'
    dl.register_private_path(filename)
    logging.getLogger('VideoOrganizer').error('failure %s %s %s %s %s', secret, 'Bearer tokenvalue', r'C:\Users\张三\客户访谈.mp4', r'\server\private\clip.mov', 'https://user:pass@example.invalid/v1?key=private')
    result = build_bundle(tmp_path / 'bundle.zip', runtime=runtime, settings={'api': {'key': secret}}, fields={'summary': filename})
    with zipfile.ZipFile(result['path']) as archive:
        contents = '\n'.join(archive.read(name).decode('utf-8') for name in archive.namelist())
        assert 'environment/runtime.json' in archive.namelist()
        assert 'logs/events.jsonl' in archive.namelist()
    for value in (secret, 'tokenvalue', '张三', '客户访谈.mp4', 'server', 'user:pass', 'key=private'):
        assert value not in contents
        assert value not in '\n'.join(p.read_text(encoding='utf-8') for p in runtime.files())
    assert result['size'] < 20 * 1024 * 1024


def test_context_isolated_between_pool_items(runtime):
    def work(number):
        with dl.operation('item', item_id=str(number)):
            dl.event('test.item')
    with dl.operation('batch'):
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(dl.contextual(work), n) for n in range(20)]
            for future in futures:
                future.result()
    events = [e for e in read_events(runtime) if e['event'] == 'test.item']
    assert {e['item_id'] for e in events} == {str(n) for n in range(20)}
    assert len({e['operation_id'] for e in events}) == 20
    assert len({e['parent_operation_id'] for e in events}) == 1


def test_worker_captures_context_and_exception(runtime):
    from gui.workers.io_worker import IoWorker
    with dl.operation('user.action'):
        worker = IoWorker(lambda: (_ for _ in ()).throw(ValueError('synthetic failure')))
    worker.run()
    errors = [e for e in read_events(runtime) if e['event'] == 'worker.failed']
    assert errors[0]['operation'] == 'IoWorker'
    assert errors[0]['exception']['type'] == 'ValueError'
    assert errors[0]['exception']['frames'][-1]['line'] > 0


def test_initialize_idempotent_relocates_and_keeps_memory(runtime, tmp_path):
    assert dl.initialize() is runtime
    dl.event('before.move')
    runtime.relocate(tmp_path / 'new-logs')
    dl.event('after.move')
    events = read_events(runtime)
    assert len([e for e in events if e['event'] == 'after.move']) == 1
    assert {e['session_id'] for e in events} == {runtime.session_id}


def test_unwritable_logs_remain_exportable(tmp_path):
    blocked = tmp_path / 'blocked'
    blocked.write_text('file')
    runtime = dl.DiagnosticRuntime(blocked)
    try:
        assert runtime.warning
        record = logging.makeLogRecord({'msg': 'synthetic fallback', 'levelno': logging.ERROR, 'levelname': 'ERROR'})
        runtime.sink.handle(record)
        result = build_bundle(tmp_path / 'memory.zip', runtime=runtime)
        with zipfile.ZipFile(result['path']) as archive:
            assert 'synthetic fallback' in archive.read('logs/events.jsonl').decode()
    finally:
        runtime.close()


def test_retention_only_removes_owned_logs(runtime):
    runtime.budget = 2048
    unrelated = runtime.directory / 'personal.txt'
    unrelated.write_bytes(b'x' * 10000)
    for n in range(8):
        (runtime.directory / f'events-old{n}.jsonl').write_bytes(b'x' * 1024)
    runtime.flush()
    runtime.prune()
    assert unrelated.exists()
    assert sum(p.stat().st_size for p in runtime.files()) <= runtime.budget


def test_bundle_cancel_preserves_destination(runtime, tmp_path):
    destination = tmp_path / 'existing.zip'
    destination.write_bytes(b'original')
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(BundleCancelled):
        build_bundle(destination, runtime=runtime, cancel=cancel)
    assert destination.read_bytes() == b'original'
    assert not list(tmp_path.glob('*.part'))


def test_corrupt_missing_database_and_long_url(runtime, tmp_path):
    (runtime.directory / f'events-{runtime.session_id}.jsonl.1').write_text('not-json\n', encoding='utf-8')
    missing = tmp_path / 'missing.db'
    result = build_bundle(tmp_path / 'partial.zip', runtime=runtime, database=missing)
    assert result['omissions']
    assert not missing.exists()
    url = issue_url({'summary': '测&?#' * 1000, 'steps': '复现' * 20000}, 'b' * 32)
    assert len(url) <= 1800
    assert 'template=bug_report.md' in url


def test_database_export_is_read_only_and_bounded(runtime, tmp_path):
    database = tmp_path / 'test.db'
    with sqlite3.connect(database) as conn:
        conn.executescript('CREATE TABLE analysis_failure_index(video_id,category,stage,status_code,model,ever400,retained_result,attempt_count); CREATE TABLE video_recovery(video_id,updated_at,state);')
        conn.executemany('INSERT INTO analysis_failure_index VALUES (?, ?, ?, ?, ?, ?, ?, ?)', [(n, 'network', 'video_classification', 503, 'model', 0, 0, 2) for n in range(250)])
        conn.executemany('INSERT INTO video_recovery VALUES (?, ?, ?)', [(n, '2026-11-10', 'failed') for n in range(250)])
    before = database.read_bytes()
    result = build_bundle(tmp_path / 'db.zip', runtime=runtime, database=database)
    assert database.read_bytes() == before
    with zipfile.ZipFile(result['path']) as archive:
        rows = json.loads(archive.read('analysis/failures.json'))
    assert len(rows) == 200
    assert 'video_id' not in rows[0]


def test_gui_result_edit_and_failed_regeneration(app, monkeypatch, tmp_path):
    from gui.widgets.diagnostic_feedback import DiagnosticFeedbackDialog
    from PySide6.QtWidgets import QApplication
    opened = []
    monkeypatch.setattr('gui.widgets.diagnostic_feedback.QDesktopServices.openUrl', lambda url: opened.append(url) or True)
    dialog = DiagnosticFeedbackDialog(SimpleNamespace(settings={}, _database_path='unused'))
    dialog.summary.setText('original')
    result = {'path': str(tmp_path / 'old.zip'), 'size': 300, 'omissions': [], 'preview': 'preview', 'body': 'old body', 'url': 'https://example.invalid'}
    dialog.completed(result)
    dialog.summary.setText('corrected')
    assert dialog.actions[0].isEnabled()
    assert not any(button.isEnabled() for button in dialog.actions[1:])
    assert '重新生成' in dialog.status.text()
    QApplication.clipboard().setText('unchanged')
    dialog.copy_body()
    dialog.open_issue()
    assert QApplication.clipboard().text() == 'unchanged' and len(opened) == 1
    dialog.failed('disk failure')
    assert dialog.actions[0].isEnabled() and not dialog.actions[1].isEnabled()
    dialog.summary.setText('original')
    for button in dialog.actions:
        button.setEnabled(False)
    dialog.cancel_event.set()
    dialog.failed('cancelled')
    assert all(button.isEnabled() for button in dialog.actions)
    assert '已取消' in dialog.status.text() and '上次诊断包仍保留' in dialog.status.text()
    dialog.reject()


def test_gui_keyboard_and_small_window_scroll(app):
    from gui.widgets.diagnostic_feedback import DiagnosticFeedbackDialog
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    dialog = DiagnosticFeedbackDialog(SimpleNamespace(settings={}, _database_path='unused'))
    dialog.resize(380, 360)
    dialog.show()
    app.processEvents()
    steps = dialog.inputs['steps']
    steps.setFocus()
    QTest.keyClick(steps, Qt.Key_Tab)
    assert dialog.inputs['expected'].hasFocus()
    assert '\t' not in steps.toPlainText()
    QTest.keyClick(dialog.inputs['expected'], Qt.Key_Backtab)
    assert steps.hasFocus()
    assert dialog.height() == 360
    assert dialog.scroll.verticalScrollBar().maximum() > 0
    point = dialog.generate.mapTo(dialog, dialog.generate.rect().center())
    assert dialog.rect().contains(point)
    assert dialog.generate.parentWidget() is dialog
    dialog.preview.setFocus()
    app.processEvents()
    assert dialog.scroll.verticalScrollBar().value() > 0
    assert dialog.preview.visibleRegion().boundingRect().height() > 0
    assert not dialog.generate.autoDefault()
    dialog.reject()


def test_gui_browser_failure_and_copy(app, monkeypatch, tmp_path):
    from gui.widgets.diagnostic_feedback import DiagnosticFeedbackDialog
    from PySide6.QtWidgets import QApplication
    dialog = DiagnosticFeedbackDialog(SimpleNamespace(settings={}, _database_path='unused'))
    monkeypatch.setattr('gui.widgets.diagnostic_feedback.QDesktopServices.openUrl', lambda _: False)
    dialog.completed({'path': str(tmp_path / 'sample.zip'), 'size': 300, 'omissions': [], 'preview': '脱敏预览', 'body': '完整反馈正文', 'url': 'https://github.com/jiahao07A/video-organizer-pro/issues/new'})
    assert '浏览器未能打开' in dialog.status.text()
    assert '尚未提交' in dialog.status.text()
    dialog.copy_body()
    assert QApplication.clipboard().text() == '完整反馈正文'
    assert all(button.isEnabled() for button in dialog.actions)
    dialog.reject()


def test_queue_congestion_keeps_error_fallback(runtime, monkeypatch):
    import queue
    runtime.flush()
    def full(_):
        raise queue.Full
    monkeypatch.setattr(runtime.queue, 'put_nowait', full)
    dl.event('congested.debug')
    dl.event('congested.error', logging.ERROR)
    assert runtime.dropped >= 2
    assert any(e['event'] == 'congested.error' for e in read_events(runtime))


def test_disk_failure_does_not_escape_and_memory_is_bounded(runtime, monkeypatch):
    runtime.flush()
    def fail(_):
        raise OSError('synthetic disk full')
    monkeypatch.setattr(runtime.handler, 'emit', fail)
    dl.event('disk.failure', logging.ERROR)
    runtime.flush()
    assert runtime.warning
    assert any(e['event'] == 'disk.failure' for e in runtime.memory)
    assert runtime.memory.maxlen == 1000


def test_memory_range_and_redacted_mapping_keys(runtime, tmp_path):
    secret = 'synthetic-secret-in-key'
    dl.register_settings({'api_key': secret})
    assert secret not in json.dumps(dl.sanitize({secret: 'value'}))
    runtime.memory.append({'time': '2000-01-01T00:00:00+00:00', 'session_id': runtime.session_id, 'event': 'ancient.event'})
    result = build_bundle(tmp_path / 'recent.zip', runtime=runtime, period='24h')
    with zipfile.ZipFile(result['path']) as archive:
        assert 'ancient.event' not in archive.read('logs/events.jsonl').decode()


def test_previous_abnormal_and_clean_exit(tmp_path):
    directory = tmp_path / 'logs'
    directory.mkdir()
    (directory / 'session-state.json').write_text(json.dumps({'session_id': 'abnormal-session', 'clean_exit': False}))
    runtime = dl.DiagnosticRuntime(directory)
    assert runtime.previous_abnormal == 'abnormal-session'
    runtime.close()
    assert json.loads((directory / 'session-state.json').read_text())['clean_exit']


def test_gui_real_background_export(app, monkeypatch, tmp_path, runtime):
    import time
    from PySide6.QtTest import QTest
    from gui.widgets.diagnostic_feedback import DiagnosticFeedbackDialog
    destination = tmp_path / 'background.zip'
    monkeypatch.setattr('gui.widgets.diagnostic_feedback.QFileDialog.getSaveFileName', lambda *a: (str(destination), 'ZIP'))
    monkeypatch.setattr('gui.widgets.diagnostic_feedback.QDesktopServices.openUrl', lambda _: False)
    dialog = DiagnosticFeedbackDialog(SimpleNamespace(settings={}, _database_path=str(tmp_path / 'missing.db')))
    dialog.summary.setText('后台导出测试')
    dialog.start()
    assert not dialog.generate.isEnabled()
    assert not dialog.summary.isEnabled()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and getattr(dialog, '_io_worker', None) is not None:
        app.processEvents()
        QTest.qWait(10)
    assert getattr(dialog, '_io_worker', None) is None
    assert destination.exists()
    assert dialog.bundle_result
    assert dialog.generate.isEnabled()
    dialog.reject()


def test_private_name_matcher_scales_and_preserves_boundaries():
    import time
    for number in range(2000):
        dl.register_private_path(f'C:/synthetic/scaling-{number}.custom')
    started = time.perf_counter()
    for _ in range(100):
        value = dl.sanitize({'event': 'test.event', 'message': 'scaling-1999.custom failed', 'module': 'module'})
    assert 'scaling-1999.custom' not in value['message']
    assert dl.sanitize('prefixscaling-1999.customsuffix') == 'prefixscaling-1999.customsuffix'
    assert time.perf_counter() - started < 2


def test_flush_failure_still_exports_and_closes(runtime, tmp_path, monkeypatch):
    runtime.flush()
    def fail():
        raise OSError('synthetic flush full')
    monkeypatch.setattr(runtime.handler, 'flush', fail)
    dl.event('before.failed.flush')
    result = build_bundle(tmp_path / 'flush-fallback.zip', runtime=runtime)
    assert result['omissions']
    assert runtime.warning
    runtime.relocate(tmp_path / 'relocated')
    monkeypatch.setattr(runtime.handler, 'flush', fail)
    runtime.close()
    monkeypatch.undo()


def test_startup_abnormal_survives_relocation(tmp_path):
    boot, data = tmp_path / 'boot', tmp_path / 'data'
    boot.mkdir()
    data.mkdir()
    (boot / 'session-state.json').write_text(json.dumps({'session_id': 'startup-crash', 'clean_exit': False, 'started_at': 200}))
    (data / 'session-state.json').write_text(json.dumps({'session_id': 'clean', 'clean_exit': True, 'previous_abnormal': 'older-crash', 'previous_abnormal_at': 100}))
    runtime = dl.DiagnosticRuntime(boot)
    try:
        runtime.relocate(data)
        assert runtime.previous_abnormal == 'startup-crash'
    finally:
        runtime.close()


def test_parseable_corrupt_log_fields_are_skipped(runtime, tmp_path):
    runtime.flush()
    path = runtime.directory / f'events-{runtime.session_id}.jsonl.1'
    base = {'session_id': runtime.session_id, 'time': '2026-11-10T00:00:00+00:00', 'event': 'valid.event', 'level': 'ERROR'}
    records = [base, {**base, 'time': None}, {**base, 'job_id': []}, {**base, 'level': {}}]
    path.write_text('\n'.join(json.dumps(e) for e in records), encoding='utf-8')
    result = build_bundle(tmp_path / 'corrupt.zip', runtime=runtime)
    assert any('无法解析' in message for message in result['omissions'])
    with zipfile.ZipFile(result['path']) as archive:
        assert 'valid.event' in archive.read('logs/events.jsonl').decode()


def test_unrelated_sessions_do_not_consume_selected_read_budget(runtime, tmp_path):
    runtime.previous_abnormal = 'selected-crash'
    unrelated = runtime.directory / 'events-other-session.jsonl'
    unrelated.write_bytes(b'x' * (25 * 1024 * 1024))
    record = {'session_id': 'selected-crash', 'time': '2026-11-10T00:00:00+00:00', 'event': 'selected.error', 'level': 'ERROR'}
    (runtime.directory / 'events-selected-crash.jsonl').write_text(json.dumps(record), encoding='utf-8')
    result = build_bundle(tmp_path / 'selected.zip', runtime=runtime)
    with zipfile.ZipFile(result['path']) as archive:
        assert 'selected.error' in archive.read('logs/events.jsonl').decode()


def test_errors_have_priority_over_many_metrics(runtime, tmp_path, monkeypatch):
    monkeypatch.setattr('core.diagnostic_bundle.LIMIT', 1024 * 1024)
    folder = runtime.directory / 'analysis_jobs'
    folder.mkdir()
    for index in range(80):
        (folder / f'job_{index}.json').write_text(json.dumps({'job_id': str(index), 'rates': {str(n): n for n in range(500)}}))
    dl.event('priority.error', logging.ERROR)
    result = build_bundle(tmp_path / 'priority.zip', runtime=runtime, period='24h')
    with zipfile.ZipFile(result['path']) as archive:
        assert 'priority.error' in archive.read('logs/events.jsonl').decode()


def test_refusal_response_body_never_enters_logs(runtime, tmp_path):
    from core.ai_gateway import _message_content
    private = 'PRIVATE_RESPONSE_BODY_敏感正文'
    try:
        _message_content(SimpleNamespace(choices=[SimpleNamespace(message={'content': None, 'refusal': private})]))
    except ValueError:
        logging.getLogger('VideoOrganizer.AI').exception('api.exception')
    result = build_bundle(tmp_path / 'refused.zip', runtime=runtime)
    with zipfile.ZipFile(result['path']) as archive:
        assert all(private not in archive.read(name).decode('utf-8') for name in archive.namelist())


@pytest.mark.parametrize('kind', ['symlink', 'junction'])
def test_retention_rejects_linked_analysis_directory(tmp_path, kind):
    import os
    import subprocess
    logs, outside = tmp_path / 'logs', tmp_path / 'outside'
    logs.mkdir()
    outside.mkdir()
    target = outside / 'job_not_owned.json'
    target.write_bytes(b'x' * 4096)
    link = logs / 'analysis_jobs'
    if kind == 'junction':
        if os.name != 'nt':
            pytest.skip('Windows junction')
        environment = {**os.environ, 'DIAGNOSTIC_TEST_LINK': str(link), 'DIAGNOSTIC_TEST_TARGET': str(outside)}
        subprocess.run(['rtk', 'proxy', 'pwsh.exe', '-NoProfile', '-Command', 'New-Item -ItemType Junction -Path $env:DIAGNOSTIC_TEST_LINK -Target $env:DIAGNOSTIC_TEST_TARGET | Out-Null'], env=environment, check=True)
    else:
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError as exc:
            pytest.skip(str(exc))
    runtime = dl.DiagnosticRuntime(logs, budget=2048)
    try:
        assert target.exists()
        assert not runtime.owns_path(link / target.name)
        assert target not in runtime.files()
        runtime.prune()
        assert target.read_bytes() == b'x' * 4096
    finally:
        runtime.close()


def test_close_and_relocate_do_not_wait_forever_on_writer_lock(runtime, tmp_path, monkeypatch):
    import time
    held, release = threading.Event(), threading.Event()
    def hold():
        with runtime.lock:
            held.set()
            release.wait(15)
    thread = threading.Thread(target=hold)
    thread.start()
    assert held.wait(1)
    monkeypatch.setattr(runtime, 'flush', lambda: None)
    started = time.monotonic()
    try:
        runtime.relocate(tmp_path / 'new')
        assert runtime.directory != tmp_path / 'new'
        runtime.close()
        assert time.monotonic() - started < 7
        assert '超时' in runtime.warning
    finally:
        release.set()
        thread.join(3)


def test_sdk_failure_summary_and_metrics_do_not_contain_body(runtime, tmp_path):
    import httpx
    from openai import BadRequestError
    from core.ai_gateway import _error_message
    from core.analysis_job_metrics import new_job_metrics, persist_job_metrics
    private = 'PRIVATE_DOWNSTREAM_RESPONSE_敏感正文'
    error = BadRequestError(private, response=httpx.Response(400, request=httpx.Request('POST', 'https://example.invalid')), body={'message': private})
    message = _error_message(error)
    logging.getLogger('VideoOrganizer').info('处理失败: %s', message)
    metrics = new_job_metrics()
    metrics.record_item_result(ok=False, reason=message)
    persist_job_metrics(metrics, log_dir=str(runtime.directory / 'analysis_jobs'))
    result = build_bundle(tmp_path / 'sdk.zip', runtime=runtime, period='24h')
    with zipfile.ZipFile(result['path']) as archive:
        assert all(private not in archive.read(name).decode('utf-8') for name in archive.namelist())


def test_caught_export_failure_contains_stack(runtime, tmp_path):
    from core.video_organizer_service import VideoOrganizerService
    service = SimpleNamespace(settings={}, db=SimpleNamespace(get_all_videos=lambda: [{'filename': 'synthetic.mp4'}]), filter_processable_rows=lambda rows: rows, log=lambda message: logging.getLogger('VideoOrganizer').info(message))
    assert VideoOrganizerService.export_to_ale(service, str(tmp_path / 'missing' / 'output.ale')) is False
    events = read_events(runtime)
    assert any(e.get('exception', {}).get('type') == 'FileNotFoundError' for e in events)


def test_identity_persists_and_bundle_uses_consistent_local_ids(runtime, tmp_path, monkeypatch):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'profile'))
    monkeypatch.setattr(dl, '_salt', None)
    source = 'C:/synthetic/shared.mp4'
    previous = dl.anonymous(source)
    monkeypatch.setattr(dl, '_salt', None)
    assert dl.anonymous(source) == previous
    old = {'session_id': 'old-crash', 'time': '2026-11-10T00:00:00+00:00', 'event': 'old.error', 'level': 'ERROR', 'item_id': previous}
    (runtime.directory / 'events-old-crash.jsonl').write_text(json.dumps(old), encoding='utf-8')
    runtime.previous_abnormal = 'old-crash'
    dl.event('current.item', item_id=dl.anonymous(source))
    result = build_bundle(tmp_path / 'identity.zip', runtime=runtime)
    with zipfile.ZipFile(result['path']) as archive:
        events = [json.loads(line) for line in archive.read('logs/events.jsonl').decode().splitlines()]
        values = {e['item_id'] for e in events if e.get('event') in ('old.error', 'current.item')}
        assert len(values) == 1
        assert next(iter(values)).startswith('asset-')
        assert all('identity.key' not in name for name in archive.namelist())


def test_congested_error_fallback_does_not_block_on_writer(runtime, monkeypatch):
    import queue
    runtime.flush()
    held, release, done = threading.Event(), threading.Event(), threading.Event()
    def hold():
        with runtime.lock:
            held.set()
            release.wait(5)
    holder = threading.Thread(target=hold)
    holder.start()
    assert held.wait(1)
    def full(_):
        raise queue.Full
    monkeypatch.setattr(runtime.queue, 'put_nowait', full)
    def log():
        dl.event('bounded.fallback', logging.ERROR)
        done.set()
    caller = threading.Thread(target=log)
    caller.start()
    try:
        assert done.wait(1)
        assert any(e['event'] == 'bounded.fallback' for e in runtime.memory)
        assert runtime.dropped
    finally:
        release.set()
        holder.join(2)
        caller.join(2)


def test_deeply_nested_log_metrics_and_marker_do_not_abort(runtime, tmp_path):
    nested = '[' * 5000 + '0' + ']' * 5000
    (runtime.directory / f'events-{runtime.session_id}.jsonl.1').write_text(nested)
    folder = runtime.directory / 'analysis_jobs'
    folder.mkdir()
    (folder / 'job_deep.json').write_text(nested)
    result = build_bundle(tmp_path / 'deep.zip', runtime=runtime, period='24h')
    assert result['omissions']
    marker_root = tmp_path / 'deep-marker'
    marker_root.mkdir()
    (marker_root / 'session-state.json').write_text(nested)
    other = dl.DiagnosticRuntime(marker_root)
    try:
        assert other.handler
    finally:
        other.close()


def test_unrelated_metrics_cannot_exhaust_event_read_budget(runtime, tmp_path):
    import os
    runtime.previous_abnormal = 'selected-metrics-crash'
    record = {'session_id': runtime.previous_abnormal, 'time': '2026-11-10T00:00:00+00:00', 'event': 'metrics.selected.error', 'level': 'ERROR'}
    path = runtime.directory / f'events-{runtime.previous_abnormal}.jsonl'
    path.write_text(json.dumps(record))
    os.utime(path, (1, 1))
    folder = runtime.directory / 'analysis_jobs'
    folder.mkdir()
    for number in range(102):
        (folder / f'job_unrelated-{number}.json').write_text(json.dumps({'job_id': f'unrelated-{number}', 'padding': 'x' * 250000}))
    result = build_bundle(tmp_path / 'metrics-selected.zip', runtime=runtime)
    with zipfile.ZipFile(result['path']) as archive:
        assert 'metrics.selected.error' in archive.read('logs/events.jsonl').decode()


@pytest.mark.parametrize('header', ['Authorization: Bearer {secret}', 'authorization=Basic {secret}', 'Authorization: Basic {secret}', 'Bearer {secret}', 'Authorization: Token {secret}', 'Authorization: Digest username=demo, response={secret}', 'Proxy-Authorization: NTLM {secret}', 'Authorization: Custom\n  {secret}'])
def test_authorization_scheme_and_credentials_redacted_before_disk_and_zip(runtime, tmp_path, header):
    secret = 'synthetic-unregistered-credential-Value123'
    text = header.format(secret=secret)
    assert secret not in dl.sanitize(text)
    logging.getLogger('VideoOrganizer').error('request failed: %s', text)
    result = build_bundle(tmp_path / 'authorization.zip', runtime=runtime)
    assert all(secret not in path.read_text(encoding='utf-8') for path in runtime.files())
    with zipfile.ZipFile(result['path']) as archive:
        assert all(secret not in archive.read(name).decode('utf-8') for name in archive.namelist())


def test_authorization_keeps_next_safe_header():
    text = dl.sanitize('Authorization: Token synthetic-secret-value\nX-Request-ID: safe-request-123')
    assert 'synthetic-secret-value' not in text
    assert 'X-Request-ID: safe-request-123' in text


@pytest.mark.parametrize('extension', ['xmp', 'XMP', 'flv', 'wmv'])
def test_sidecar_and_supported_video_names_redacted_on_disk_and_export(runtime, tmp_path, extension):
    dl.register_private_path('PRIVATE_CUSTOMER.mp4')
    old, new = f'PRIVATE_CUSTOMER.{extension}', f'PRIVATE_RESULT.{extension}'
    logging.getLogger('VideoOrganizer').info('同步重命名: %s -> %s', old, new)
    result = build_bundle(tmp_path / 'sidecar.zip', runtime=runtime)
    disk = '\n'.join(path.read_text(encoding='utf-8') for path in runtime.files())
    assert old not in disk and new not in disk
    with zipfile.ZipFile(result['path']) as archive:
        contents = '\n'.join(archive.read(name).decode('utf-8') for name in archive.namelist())
    assert old not in contents and new not in contents


def test_real_catalog_scan_propagates_context_to_thumbnail_pool(runtime, tmp_path):
    from core.video_organizer_service import VideoOrganizerService
    sources = [str(tmp_path / f'synthetic-{number}.mp4') for number in range(4)]
    def extract(path, **kwargs):
        dl.event('synthetic.thumbnail', expected_item=dl.anonymous(path))
        raise ValueError('synthetic decode failure')
    service = SimpleNamespace(
        settings={'processing': {'extract_workers': 4}}, _work_scope_paths=[str(tmp_path)],
        file_manager=SimpleNamespace(scan_videos=lambda path: sources),
        db=SimpleNamespace(get_video_by_path=lambda path: None),
        media_registry=SimpleNamespace(register=lambda path, **_: {'path': path, 'filename': path}),
        processor=SimpleNamespace(extract_frames=extract), _thumbnail_dir=str(tmp_path),
        log=lambda message: None, invalidate_video_memory_cache=lambda: None)
    assert VideoOrganizerService.scan_and_register_work_scope(service) == sources
    events = read_events(runtime)
    operation = next(e for e in events if e.get('operation') == 'catalog.scan' and e['event'] == 'operation.start')
    pool = [e for e in events if e['event'] == 'synthetic.thumbnail']
    assert len(pool) == 4
    assert all(e['operation_id'] == operation['operation_id'] for e in pool)
    assert all(e['item_id'] == e['expected_item'] for e in pool)
    failures = [e for e in events if e.get('exception', {}).get('type') == 'ValueError']
    assert len(failures) == 4
    assert all(e['operation_id'] == operation['operation_id'] and e['item_id'] for e in failures)


def test_metrics_persistence_does_not_wait_for_busy_log_lock(runtime, tmp_path):
    import time
    from core.analysis_job_metrics import new_job_metrics, persist_job_metrics
    held, release, done = threading.Event(), threading.Event(), threading.Event()
    result = []
    def hold():
        with runtime.lock:
            held.set()
            release.wait(5)
    holder = threading.Thread(target=hold)
    holder.start()
    assert held.wait(1)
    def persist():
        result.append(persist_job_metrics(new_job_metrics(), log_dir=str(tmp_path / 'metrics')))
        done.set()
    caller = threading.Thread(target=persist)
    started = time.monotonic()
    caller.start()
    try:
        assert done.wait(1)
        assert time.monotonic() - started < 1
        assert result and '清理延后' in runtime.warning
    finally:
        release.set()
        caller.join(2)
        holder.join(2)


@pytest.mark.parametrize('link', ['operation_id', 'job_id', 'request_id', 'parent_operation_id'])
def test_bundle_capacity_preserves_error_context(runtime, tmp_path, monkeypatch, link):
    monkeypatch.setattr('core.diagnostic_bundle.LIMIT', 1024 * 1024)
    runtime.flush()
    base = {'session_id': runtime.session_id, 'time': '2026-11-10T00:00:00+00:00', 'level': 'INFO'}
    context_key = 'operation_id' if link == 'parent_operation_id' else link
    records = [{**base, 'event': 'failed.start', context_key: 'failed-link'},
               {**base, 'event': 'failed.error', 'level': 'ERROR', link: 'failed-link'}]
    records.extend({**base, 'time': '2026-11-10T00:01:00+00:00', 'event': f'unrelated.progress.{n}',
                    context_key: f'other-{n}', 'message': 'ordinary progress ' * 900} for n in range(80))
    (runtime.directory / f'events-{runtime.session_id}.jsonl.1').write_text(
        '\n'.join(json.dumps(record) for record in records), encoding='utf-8')
    result = build_bundle(tmp_path / 'correlated.zip', runtime=runtime)
    with zipfile.ZipFile(result['path']) as archive:
        events = [json.loads(line) for line in archive.read('logs/events.jsonl').decode().splitlines()]
    names = {event['event'] for event in events}
    assert {'failed.start', 'failed.error'} <= names
    assert len(names) < len(records)
    assert any('关联事件' in omission for omission in result['omissions'])
