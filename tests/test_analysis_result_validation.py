"""Issue #73：分析结果可信边界及下游文件操作回归。"""
import json
from types import SimpleNamespace

import pytest

from tests.support.recovery import service as _service_fixture, video, clients as recovery_clients

service = _service_fixture


@pytest.fixture(autouse=True)
def forbid_external_requests(monkeypatch):
    """测试只能消费明确注入的响应，意外外网请求直接使测试失败。"""
    def blocked(*args, **kwargs):
        pytest.fail('测试意外尝试访问外部网络')
    monkeypatch.setattr('httpx.Client.send', blocked)


def clients(service, monkeypatch, **outputs):
    result = recovery_clients(service, monkeypatch, **outputs)
    monkeypatch.setattr(service.ai.gateway, 'client_for_task',
        lambda task: (result['main'], service.ai.resolve_task_route(task)))
    service.ai._retry_sleeper = lambda _: None
    return result


def reply(payload):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
        content=json.dumps(payload, ensure_ascii=False)))])


def test_failure_summary_is_failed_not_cached_or_used_for_rename(service, tmp_path, monkeypatch):
    path = video(service, tmp_path)
    connection = clients(service, monkeypatch, main=[reply({
        'category': 'Other', 'summary': '抽帧失败，未能获取视频画面内容',
        'tags': [], 'tag_groups': {},
    })])['main']
    result = service.run_analysis([path])
    assert result['succeeded'] == 0 and result['failed'] == 1
    assert service.db.get_video_by_path(path)['status'] == 'failed'
    assert service.db.execute_query('SELECT * FROM api_cache') == []
    failures = service.query_analysis_failures()
    assert failures['total'] == 1
    assert failures['rows'][0]['category'] == 'response'
    assert failures['rows'][0]['stage'] == 'video_classification'
    # 默认 A 层 3 次 × B 层 2 次 × C 层 2 轮，有明确上限。
    assert len(connection.calls) == 12
    assert result['metrics']['api_success'] == 0
    assert result['metrics']['api_fail'] == 12


@pytest.mark.parametrize('payload', [
    {'summary': '街头行人'}, {'category': 'Other'},
    {'category': 'Other', 'summary': ''}, {'category': 'Other', 'summary': '   '},
    {'category': 'Other', 'summary': None}, {'category': 'Other', 'summary': 42},
    {'category': [], 'summary': '街头行人'},
    {'category': 'Other', 'summary': '街头行人', 'tags': '行人'},
    {'category': 'Other', 'summary': '街头行人', 'tags': [False]},
    {'category': 'Other', 'summary': '街头行人', 'tag_groups': []},
    {'category': 'Other', 'summary': '街头行人', 'tag_groups': {'mood': {'bad': 'tag'}}},
    {'category': 'Other', 'summary': '街头行人', 'success': False},
    {'category': 'Other', 'summary': '街头行人', 'error': {'message': 'upstream error'}},
    {'category': 'Other', 'summary': '街头行人', 'status': 'failed'},
    {'category': 'Other', 'summary': '街头行人', 'refusal': 'cannot process'},
    ['街头行人'], '街头行人', True, 42,
])
def test_malformed_results_are_response_failures(service, tmp_path, monkeypatch, payload):
    service.settings['processing']['analysis_retry'].update(call_extra_attempts=0, item_max_attempts=1, batch_rerun_enabled=False)
    path = video(service, tmp_path)
    clients(service, monkeypatch, main=[reply(payload)])
    result = service.run_analysis([path])
    assert result['succeeded'] == 0 and result['failed'] == 1
    assert service.db.execute_query('SELECT * FROM api_cache') == []
    failure = service.query_analysis_failures()['rows'][0]
    assert failure['category'] == 'response' and failure['stage'] == 'video_classification'


@pytest.mark.parametrize('summary', [
    '抽帧失败，未能获取视频画面内容', '无法提取视频帧，未能完成分析',
    '未能识别图片内容。', '无法分析该视频的内容', '抱歉，我无法查看提供的图片。',
    'Failed to extract video frames.', 'I cannot view the provided images.',
    'Frame extraction failed.', '未知', 'unknown', '\u200b抽帧失败，未能读取画面',
    '无法查看图片。手机屏幕上有错误提示。',
    'I cannot view images. A phone screen is shown.',
    '抱歉，无法查看图片的错误提示出现在手机屏幕上。',
    '我无法查看图片的错误提示出现在手机屏幕上。',
])
def test_model_admission_of_failure_is_not_a_summary(service, monkeypatch, summary):
    clients(service, monkeypatch, main=[reply({'category': 'Other', 'summary': summary})])
    service.ai._retry_sleeper = lambda _: None
    assert service.ai.analyze_video(['fake-jpeg'], cache_key='failure') is None
    assert service.db.get_cache('failure') is None


@pytest.mark.parametrize('summary', [
    '程序员修复抽帧失败的问题，屏幕显示错误提示。',
    '屏幕上显示“抽帧失败，未能获取视频画面内容”的提示。',
    '抽帧失败的提示出现在电脑屏幕上。',
    '无法查看图片的错误提示出现在手机屏幕上。',
    '无法分析视频的提示被显示在电脑界面上。',
    '男子尝试跳跃失败，随后站起来继续训练。',
    '无法打开的门前站着一位穿红衣的人。',
    'A screen displays "Frame extraction failed" beside a video editor.',
    'Frame extraction failed message displayed on a computer screen.',
    'Cannot view images warning is shown on a phone screen.',
    'A person fails to jump over a wall.', '黑色画面，无明显主体。',
])
def test_real_descriptions_mentioning_failure_remain_valid(service, monkeypatch, summary):
    connection = clients(service, monkeypatch, main=[reply({'category': 'Other', 'summary': summary})])['main']
    result = service.ai.analyze_video(['fake-jpeg'], cache_key='good')
    assert result['summary'] == summary
    assert service.ai.analyze_video(['fake-jpeg'], cache_key='good')['summary'] == summary
    assert len(connection.calls) == 1


def test_invalid_persistent_cache_is_discarded_and_refetched(service, monkeypatch):
    service.db.set_cache('key', {'category': 'Other', 'summary': '抽帧失败，未能获取视频画面内容'})
    connection = clients(service, monkeypatch, main=[reply({'category': 'Other', 'summary': '行人穿过街道'})])['main']
    result = service.ai.analyze_video(['fake-jpeg'], cache_key='key')
    assert result['summary'] == '行人穿过街道'
    assert len(connection.calls) == 1
    assert service.db.get_cache('key')['summary'] == '行人穿过街道'


def test_error_summary_retries_then_commits_only_valid_result(service, tmp_path, monkeypatch):
    path = video(service, tmp_path)
    connection = clients(service, monkeypatch, main=[
        reply({'category': 'Other', 'summary': '抽帧失败，未能获取视频画面内容'}),
        reply({'category': 'Other', 'summary': '行人穿过街道'}),
    ])['main']
    result = service.run_analysis([path])
    assert result['succeeded'] == 1 and result['failed'] == 0
    assert len(connection.calls) == 2
    assert service.db.get_video_by_path(path)['summary'] == '行人穿过街道'
    assert service.query_analysis_failures()['total'] == 0


def test_failed_reanalysis_retains_success_and_has_no_media_side_effects(service, tmp_path, monkeypatch):
    path = video(service, tmp_path, status='analyzed')
    service.settings['processing'].update(enable_metadata_injection=True, auto_sync_xmp_after_analysis=True)
    clients(service, monkeypatch, main=[reply({'category': 'Other', 'summary': '抽帧失败，未能获取视频画面内容', 'mood': ['不应写入待审']} )])
    effects = []
    monkeypatch.setattr(service, 'sync_metadata_to_xmp', lambda *a, **kw: effects.append('xmp'))
    monkeypatch.setattr('core.video_organizer_service.MetadataInjector.inject_tags', lambda *a, **kw: effects.append('metadata'))
    before = service.db.get_video_by_path(path)
    result = service.run_analysis([path], force_reanalyze=True)
    after = service.db.get_video_by_path(path)
    assert result['succeeded'] == 0 and result['failed'] == 1
    assert all(before[k] == after[k] for k in ('summary', 'tags', 'category', 'status', 'raw_metadata'))
    assert effects == []
    assert service.db.list_pending_tags() == []
    assert service.query_analysis_failures()['rows'][0]['retained_result'] == 1


def test_old_error_summary_cannot_rename_but_valid_sibling_can(service, tmp_path):
    bad = video(service, tmp_path, name='bad.mp4', status='analyzed')
    good = video(service, tmp_path, name='good.mp4', status='analyzed')
    # 独立内容，不让去重逻辑混淆重命名防线。
    from pathlib import Path
    Path(good).write_bytes(b'good-distinct-video')
    service.db.upsert_video({**service.db.get_video_by_path(bad), 'summary': '抽帧失败，未能获取视频画面内容'})
    service.db.upsert_video({**service.db.get_video_by_path(good), 'summary': '行人穿过街道'})
    for dry_run in (True, False):
        previews = service.batch_rename(dry_run=dry_run, selected_paths=[bad, good], custom_pattern='{summary}')
        if dry_run:
            assert [x['new'] for x in previews] == ['行人穿过街道.mp4']
        report = service._last_rename_result
        assert report['failed_count'] == 1 and report['success_count'] == 1
        assert report['failed_items'][0]['path'] == bad
        assert Path(bad).is_file()
    assert (tmp_path/'行人穿过街道.mp4').is_file()


def test_error_summary_still_allows_explicit_original_name_pattern(service, tmp_path):
    path = video(service, tmp_path, status='analyzed')
    service.db.upsert_video({**service.db.get_video_by_path(path), 'summary': '抽帧失败，未能获取视频画面内容'})
    result = service.batch_rename(dry_run=True, selected_paths=[path], custom_pattern='safe-{original_name}')
    assert result[0]['new'] == 'safe-v.mp4'


@pytest.mark.parametrize('corrupt', ['{broken', 'null', '[]', 'false', '{"category":"Other"}'])
def test_corrupt_cache_does_not_prevent_fresh_analysis(service, monkeypatch, corrupt):
    service.db.execute_non_query('INSERT INTO api_cache(cache_key,response) VALUES (?,?)', ('key', corrupt))
    connection = clients(service, monkeypatch, main=[reply({'category': 'Other', 'summary': '街头行人'})])['main']
    assert service.ai.analyze_video(['fake-jpeg'], cache_key='key')['summary'] == '街头行人'
    assert len(connection.calls) == 1


def test_invalid_response_is_never_reused_in_recovery_context(service, tmp_path, monkeypatch):
    from core.analysis_recovery import RecoveryContext, effective_policy
    path = video(service, tmp_path)
    policy, _ = effective_policy(service.settings)
    ctx = RecoveryContext(path, service.settings, policy, service.recovery_store)
    ctx.analysis_data = {'category': 'Other', 'summary': '抽帧失败，未能获取视频画面内容'}
    connection = clients(service, monkeypatch, main=[reply({'category': 'Other', 'summary': '街头行人'})])['main']
    service.ai.gateway._tls.recovery_context = ctx
    try:
        first = service.ai.analyze_video(['fake-jpeg'], cache_key='context')
        first['summary'] = '调用方修改了副本'
        assert service.ai.analyze_video(['fake-jpeg'], cache_key='context')['summary'] == '街头行人'
        assert len(connection.calls) == 1
    finally:
        service.ai.gateway._tls.recovery_context = None


@pytest.mark.parametrize('group_value', ['晴天', ['晴天', '傍晚'], [], '', None])
def test_valid_flat_and_nested_tag_shapes_remain_compatible(service, monkeypatch, group_value):
    service.ai.tag_config = {'tag_groups': [{'id': 'scene', 'name': '场景', 'rules': {'ai_expandable': True, 'max_count': 3}}]}
    clients(service, monkeypatch, main=[reply({'category': 'Other', 'summary': '街头行人', 'scene': group_value})])
    assert service.ai.analyze_video(['fake-jpeg'], cache_key='flat')['summary'] == '街头行人'
    clients(service, monkeypatch, main=[reply({'category': 'Other', 'summary': '街头行人', 'tag_groups': {'scene': group_value}})])
    assert service.ai.analyze_video(['fake-jpeg'], cache_key='nested')['summary'] == '街头行人'


def test_invalid_flat_group_is_rejected_before_normalization(service, tmp_path, monkeypatch):
    service.ai.tag_config = {'tag_groups': [{'id': 'scene', 'name': '场景'}]}
    path = video(service, tmp_path)
    clients(service, monkeypatch, main=[reply({'category': 'Other', 'summary': '街头行人', 'scene': {'nested': 'wrong'}})])
    result = service.run_analysis([path])
    assert result['succeeded'] == 0 and result['failed'] == 1
    assert service.db.execute_query('SELECT * FROM api_cache') == []


def test_cancel_after_invalid_reply_does_not_retry(service, tmp_path, monkeypatch):
    path = video(service, tmp_path)
    def cancel_then_reply():
        service.request_cancel_analysis()
        return reply({'category': 'Other', 'summary': '抽帧失败，未能获取视频画面内容'})
    connection = clients(service, monkeypatch, main=[cancel_then_reply])['main']
    result = service.run_analysis([path])
    assert result['succeeded'] == 0 and result['cancelled']
    assert len(connection.calls) == 1
    assert service.db.execute_query('SELECT * FROM api_cache') == []


def test_manual_recovery_of_invalid_results_respects_request_budget(service, tmp_path, monkeypatch):
    path = video(service, tmp_path)
    service.settings['processing']['http400_recovery']['global']['request_budget'] = 2
    connection = clients(service, monkeypatch, main=[reply({'category': 'Other', 'summary': '抽帧失败，未能获取视频画面内容'})])['main']
    result = service.run_analysis([path], force_reanalyze=True, recovery_mode='primary', manual_options={'request_interval': 0})
    assert result['succeeded'] == 0 and result['failed'] == 1
    assert len(connection.calls) == 2
    failure = service.query_analysis_failures()['rows'][0]
    assert failure['category'] == 'response' and not failure['ever400']


def test_invalid_payload_is_not_copied_to_failure_logs_or_pending_tags(service, tmp_path, monkeypatch, caplog):
    path = video(service, tmp_path)
    secret = 'sk-probe-do-not-log-this-credential'
    service.ai.tag_config = {'tag_groups': [{'id': 'scene', 'name': '场景', 'rules': {'ai_expandable': True}}]}
    clients(service, monkeypatch, main=[reply({'category': 'Other', 'summary': {'secret': secret}, 'scene': ['不应进入待审']})])
    before = service.db.get_tags_detail()
    service.run_analysis([path])
    assert secret not in caplog.text
    assert secret not in json.dumps(service.get_recovery_detail(path))
    assert service.db.list_pending_tags() == []
    assert service.db.get_tags_detail() == before


def test_manual_summary_correction_unblocks_old_error_metadata(service, tmp_path):
    path = video(service, tmp_path, status='analyzed')
    bad = {'category': 'Other', 'summary': '抽帧失败，未能获取视频画面内容', 'success': False}
    service.db.upsert_video({**service.db.get_video_by_path(path), 'summary': bad['summary'], 'raw_metadata': bad})
    service.update_video_metadata(path, {'summary': '街头行人'})
    previews = service.batch_rename(dry_run=True, selected_paths=[path], custom_pattern='{summary}')
    assert previews[0]['new'] == '街头行人.mp4'


def test_rename_dialog_excludes_invalid_results_and_allows_safe_pattern(service, tmp_path, app):
    from gui.widgets.rename_dialog import BatchRenameDialog
    path = video(service, tmp_path, status='analyzed')
    item = {**service.db.get_video_by_path(path), 'summary': '抽帧失败，未能获取视频画面内容'}
    dialog = BatchRenameDialog(service, [item])
    try:
        dialog.pattern_input.setText('{summary}')
        assert dialog.selected_source_paths() == []
        assert not dialog.apply_btn.isEnabled()
        assert '已阻止 1 条' in dialog.preview_summary_label.text()
        dialog.pattern_input.setText('safe-{original_name}')
        assert dialog.selected_source_paths() == [path]
        assert dialog.apply_btn.isEnabled()
    finally:
        dialog.close()


def test_rename_worker_reports_invalid_result_failure(service, tmp_path, app):
    from gui.workers.rename_worker import RenameWorker
    path = video(service, tmp_path, status='analyzed')
    service.db.upsert_video({**service.db.get_video_by_path(path), 'summary': '抽帧失败，未能获取视频画面内容'})
    worker = RenameWorker(service, selected_paths=[path], pattern='{summary}')
    finished = []
    worker.task_finished.connect(lambda ok, message: finished.append((ok, message)))
    worker.run()
    assert len(finished) == 1 and finished[0][0] is False
    assert '失败 1 项' in finished[0][1]


def test_monitor_keeps_invalid_old_analysis_in_review(service, tmp_path):
    from core.media_watch import MediaDirectoryMonitor
    from core.watch_config import normalize_watch_config
    from tests.test_media_watch_workspace import wait_monitor
    from pathlib import Path
    path = Path(video(service, tmp_path, status='analyzed'))
    service.db.upsert_video({**service.db.get_video_by_path(str(path)), 'summary': '抽帧失败，未能获取视频画面内容'})
    config = normalize_watch_config({'auto_rename': True, 'rename_pattern': '{summary}', 'xmp_mode': 'off',
        'directories': [{'path': str(tmp_path)}]})
    monitor = MediaDirectoryMonitor(service, config)
    try:
        monitor._process_video(str(path))
        monitor._dispatch_batch()
        wait_monitor(monitor)
        item = service.media_registry.items()[0]
        assert item['stage'] == 'review' and '无效分析结果' in item['error']
        assert path.is_file()
        assert service.db.execute_query('SELECT * FROM rename_history') == []
    finally:
        monitor.stop(shutdown_analysis=True)


def test_parallel_results_do_not_mix_success_failure_or_cache(service, tmp_path, monkeypatch):
    from pathlib import Path
    from threading import Barrier
    service.settings['processing']['analysis_retry'].update(call_extra_attempts=0, item_max_attempts=1, batch_rerun_enabled=False)
    service.settings['processing']['max_workers'] = 4
    paths = [video(service, tmp_path, name=f'parallel{i}.mp4') for i in range(4)]
    for i, path in enumerate(paths):
        Path(path).write_bytes(f'parallel-distinct-{i}'.encode())
    monkeypatch.setattr(service.processor, 'extract_frames', lambda path, **kw: {'frames': [Path(path).stem], 'thumbnail': None, 'phash': ''})
    barrier = Barrier(4)
    connection = clients(service, monkeypatch, main=[reply({})])['main']
    def create(**kwargs):
        parts = kwargs['messages'][1]['content']
        encoded = parts[1]['image_url']['url']
        barrier.wait(timeout=5)
        failed = encoded.endswith('parallel1') or encoded.endswith('parallel3')
        summary = '抽帧失败，未能获取视频画面内容' if failed else encoded.rsplit(',', 1)[-1]
        return reply({'category': 'Other', 'summary': summary})
    connection.chat.completions.create = create
    result = service.run_analysis(paths)
    assert result['succeeded'] == result['failed'] == 2
    assert {row['path'] for row in service.query_analysis_failures()['rows']} == {paths[1], paths[3]}
    for i, path in enumerate(paths):
        row = service.db.get_video_by_path(path)
        assert row['status'] == ('failed' if i % 2 else 'analyzed')
        if i % 2 == 0:
            assert row['summary'] == f'parallel{i}'
    cache = service.db.execute_query('SELECT response FROM api_cache')
    assert len(cache) == 2
    assert {json.loads(row['response'])['summary'] for row in cache} == {'parallel0', 'parallel2'}


def test_real_decode_to_rename_blocks_bad_summary_and_keeps_good_video(service, tmp_path, monkeypatch):
    import cv2
    import numpy as np
    from core.video_organizer_service import VideoProcessor
    # 恢复真实抽帧接缝，仅远程 API 模拟，不调用任何收费服务。
    monkeypatch.setattr(service.processor, 'extract_frames', VideoProcessor.extract_frames.__get__(service.processor))
    service.settings['processing'].update(enable_scene_detection=False, max_frames=8)
    service.settings['processing']['analysis_retry'].update(call_extra_attempts=0, item_max_attempts=1, batch_rerun_enabled=False)
    paths = []
    for i in range(2):
        path = tmp_path/f'real{i}.avi'
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'MJPG'), 8, (64, 64))
        assert writer.isOpened()
        for _ in range(16):
            writer.write(np.full((64, 64, 3), 50 + 100*i, dtype=np.uint8))
        writer.release()
        paths.append(str(path))
    connection = clients(service, monkeypatch, main=[reply({'category': 'Other', 'summary': '抽帧失败，未能获取视频画面内容'})])['main']
    first = service.run_analysis(paths[:1])
    assert first['failed'] == 1 and first['succeeded'] == 0
    assert len(connection.calls[0]['messages'][1]['content']) == 9
    assert service.batch_rename(dry_run=True, selected_paths=paths[:1], custom_pattern='{summary}') == []
    assert service._last_rename_result['failed_count'] == 1
    connection = clients(service, monkeypatch, main=[reply({'category': 'Other', 'summary': '灰色静态画面'})])['main']
    second = service.run_analysis(paths[1:])
    assert second['succeeded'] == 1 and second['failed'] == 0
    service.batch_rename(selected_paths=paths[1:], custom_pattern='{summary}')
    assert (tmp_path/'灰色静态画面.avi').is_file()
    assert not (tmp_path/'抽帧失败，未能获取视频画面内容.avi').exists()


def test_empty_rename_does_not_reuse_previous_failure_report(service, tmp_path):
    path = video(service, tmp_path, status='analyzed')
    service.db.upsert_video({**service.db.get_video_by_path(path), 'summary': '抽帧失败，未能获取视频画面内容'})
    service.batch_rename(dry_run=True, selected_paths=[path], custom_pattern='{summary}')
    assert service._last_rename_result['failed_count'] == 1
    assert service.batch_rename(dry_run=True, selected_paths=[]) == []
    assert service._last_rename_result['failed_count'] == 0
    assert service._last_rename_result['success_count'] == 0


def test_empty_summary_cannot_generate_extension_only_filename(service, tmp_path):
    path = video(service, tmp_path, status='analyzed')
    service.db.upsert_video({**service.db.get_video_by_path(path), 'summary': ''})
    assert service.batch_rename(dry_run=True, selected_paths=[path], custom_pattern='{summary}') == []
    assert service._last_rename_result['failed_count'] == 1


def test_failed_analysis_can_only_use_original_name_until_corrected(service, tmp_path):
    path = video(service, tmp_path, status='failed')
    assert service.batch_rename(dry_run=True, selected_paths=[path], custom_pattern='{summary}') == []
    assert service._last_rename_result['failed_count'] == 1
    assert service.batch_rename(dry_run=True, selected_paths=[path], custom_pattern='safe-{original_name}')


@pytest.mark.parametrize('group_id', ['status', 'success', 'ok', 'error', 'error_message', 'refusal'])
@pytest.mark.parametrize('value', ['failed', ['晴天']])
def test_protocol_named_groups_remain_valid_through_analysis_cache_and_rename(service, tmp_path, monkeypatch, group_id, value):
    service.settings['processing']['analysis_retry'].update(call_extra_attempts=0, item_max_attempts=1, batch_rerun_enabled=False)
    service.tag_config['tag_groups'] = [{
        'id': group_id, 'name': '自定义组', 'rules': {'ai_expandable': True, 'max_count': 3},
    }]
    service.ai.tag_config = service.tag_config
    path = video(service, tmp_path)
    connection = clients(service, monkeypatch, main=[reply({
        'category': 'Other', 'summary': '街头行人', group_id: value,
    })])['main']
    result = service.run_analysis([path])
    assert result['succeeded'] == 1 and result['failed'] == 0
    row = service.db.get_video_by_path(path)
    expected = [value] if isinstance(value, str) else value
    assert row['tag_groups'][group_id] == expected
    assert service.run_analysis([path], force_reanalyze=False)['failed'] == 0
    # 显式走持久缓存，避免“已分析跳过”掩盖缓存复核问题。
    cache_key = service.db.execute_query('SELECT cache_key FROM api_cache')[0]['cache_key']
    cached = service.ai.analyze_video(['fake-jpeg'], cache_key=cache_key)
    assert cached['summary'] == '街头行人'
    from core.analysis_recovery import RecoveryContext, effective_policy
    policy, _ = effective_policy(service.settings)
    ctx = RecoveryContext(path, service.settings, policy, service.recovery_store)
    ctx.analysis_data = cached
    service.ai.gateway._tls.recovery_context = ctx
    try:
        assert service.ai.analyze_video(['fake-jpeg'], cache_key='context')['summary'] == '街头行人'
    finally:
        service.ai.gateway._tls.recovery_context = None
    assert len(connection.calls) == 1
    service.tag_config['tag_groups'] = []
    assert service.batch_rename(dry_run=True, selected_paths=[path], custom_pattern='{summary}')[0]['new'] == '街头行人.mp4'


@pytest.mark.parametrize('group_id', ['status', 'success', 'ok', 'error', 'error_message', 'refusal'])
def test_protocol_named_group_does_not_hide_model_failure(service, tmp_path, monkeypatch, group_id):
    service.settings['processing']['analysis_retry'].update(call_extra_attempts=0, item_max_attempts=1, batch_rerun_enabled=False)
    service.ai.tag_config = {'tag_groups': [{'id': group_id, 'rules': {'ai_expandable': True}}]}
    path = video(service, tmp_path)
    clients(service, monkeypatch, main=[reply({
        'category': 'Other', 'summary': '抽帧失败，未能获取视频画面内容', group_id: '晴天',
    })])
    result = service.run_analysis([path])
    assert result['succeeded'] == 0 and result['failed'] == 1
    assert service.db.execute_query('SELECT * FROM api_cache') == []


@pytest.mark.parametrize('pattern,find,replace', [
    ('{summary}.mp4', None, ''), ('{original_name}', r'^.*(?=\.mp4$)', ''),
    ('--.MP4', None, ''),
])
def test_explicit_extension_only_names_are_blocked(service, tmp_path, pattern, find, replace):
    path = video(service, tmp_path, status='analyzed')
    service.db.upsert_video({**service.db.get_video_by_path(path), 'summary': ''})
    assert service.batch_rename(dry_run=True, selected_paths=[path], custom_pattern=pattern,
                                regex_find=find, regex_replace=replace) == []
    assert service._last_rename_result['failed_count'] == 1


@pytest.mark.parametrize('consumer', ['monitor', 'worker'])
def test_concurrent_rename_cannot_replace_consumer_failure_report(service, tmp_path, monkeypatch, app, consumer):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from core.media_watch import MediaDirectoryMonitor
    from core.watch_config import normalize_watch_config
    from gui.workers.rename_worker import RenameWorker

    path = video(service, tmp_path, status='analyzed')
    service.db.upsert_video({**service.db.get_video_by_path(path), 'summary': '抽帧失败，未能获取视频画面内容'})
    preview_done, interference_attempted = Event(), Event()
    original = service.batch_rename

    def intercepted(**kwargs):
        result = original(**kwargs)
        preview_done.set()
        assert interference_attempted.wait(5)
        return result

    monkeypatch.setattr(service, 'batch_rename', intercepted)

    def competitor():
        assert preview_done.wait(5)
        # 不依赖任意等待时长：能取得锁就先完成覆盖，否则先通知调用方再等待文件锁。
        acquired = service._file_operation_lock.acquire(blocking=False)
        if acquired:
            try:
                original(dry_run=True, selected_paths=[])
            finally:
                service._file_operation_lock.release()
                interference_attempted.set()
        else:
            interference_attempted.set()
            original(dry_run=True, selected_paths=[])

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(competitor)
        if consumer == 'monitor':
            monitor = MediaDirectoryMonitor(service, normalize_watch_config({}))
            updates = []
            monkeypatch.setattr(monitor.registry, 'update_item', lambda *args: updates.append(args))
            try:
                options = {'rename_pattern': '{summary}', 'regex_find': '', 'regex_replace': '', 'rename_conflict': 'review'}
                assert monitor._rename_item({'id': 1}, path, options,
                                            {'id': 'batch', 'created_at': '2026-11-13T12:00:00+08:00'}) is False
                assert updates and updates[0][1] == 'review'
            finally:
                monitor.stop(shutdown_analysis=True)
        else:
            worker = RenameWorker(service, selected_paths=[path], pattern='{summary}')
            finished = []
            worker.task_finished.connect(lambda ok, message: finished.append((ok, message)))
            worker.run()
            assert len(finished) == 1 and finished[0][0] is False
            assert '失败 1 项' in finished[0][1]
        future.result(timeout=5)
