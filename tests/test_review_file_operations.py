"""审查发现的文件操作一致性回归；只使用合成文件和临时 SQLite。"""
import os
from pathlib import Path

import pytest

from core import video_organizer_service as module


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setattr(module, 'TAG_CONFIG_FILE', str(tmp_path / 'tags.json'))
    monkeypatch.setattr(module, 'get_settings_file_path', lambda: str(tmp_path / 'settings.json'))
    return module.VideoOrganizerService(settings=module.SettingsManager.deep_copy_defaults(),
        on_log=lambda _: None, db_path=str(tmp_path / 'catalog.db'),
        results_json=str(tmp_path / 'results.json'), results_csv=str(tmp_path / 'results.csv'))


def add_video(service, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(('synthetic ' + path.name).encode())
    row = service.media_registry.register(str(path))
    service.db.upsert_video({**row, 'status': 'analyzed', 'category': 'Broll',
        'summary': '合成画面', 'tags': ['测试']})
    return row['id']


def rename_video(service, path):
    add_video(service, path)
    path.with_suffix('.xmp').write_text('<synthetic/>', encoding='utf-8')
    service.set_work_scope_paths([str(path)], scan=False)
    service.batch_rename(selected_paths=[str(path)], custom_pattern='new-{original_name}')
    assert service._last_rename_result['success_count'] == 1
    return path.with_name('new-' + path.name), service._last_rename_result['session_id']


def test_rollback_database_failure_compensates_video_xmp_and_can_retry(service, tmp_path, monkeypatch):
    old = tmp_path / 'clip.mp4'
    new, session = rename_video(service, old)
    original_update = service.db.update_video_path
    monkeypatch.setattr(service.db, 'update_video_path', lambda *a, **k: (_ for _ in ()).throw(OSError('database denied')))
    assert not service.rollback_last_session(session)
    assert new.exists() and new.with_suffix('.xmp').exists()
    assert not old.exists() and not old.with_suffix('.xmp').exists()
    assert service.db.get_video_by_path(str(new)) is not None
    assert service.get_work_scope_paths() == [str(new)]
    assert len(service.db.get_history_by_session(session)) == 1
    monkeypatch.setattr(service.db, 'update_video_path', original_update)
    assert service.rollback_last_session(session)
    assert old.exists() and old.with_suffix('.xmp').exists()
    assert service.db.get_video_by_path(str(old)) is not None
    assert service.get_work_scope_paths() == [str(old)]


def test_rollback_update_that_committed_before_error_is_not_blindly_compensated(service, tmp_path, monkeypatch):
    old = tmp_path / 'clip.mp4'
    new, session = rename_video(service, old)
    update = service.db.update_video_path

    def commit_then_raise(*args, **kwargs):
        update(*args, **kwargs)
        raise OSError('after commit')

    monkeypatch.setattr(service.db, 'update_video_path', commit_then_raise)
    assert service.rollback_last_session(session)
    assert old.exists() and old.with_suffix('.xmp').exists()
    assert not new.exists() and not new.with_suffix('.xmp').exists()
    assert service.db.get_video_by_path(str(old)) is not None
    assert service.db.get_history_by_session(session) == []


@pytest.mark.parametrize('sidecar_conflict', [False, True])
def test_rollback_xmp_failure_keeps_video_and_history_retryable(service, tmp_path, monkeypatch, sidecar_conflict):
    old = tmp_path / 'clip.mp4'
    new, session = rename_video(service, old)
    rename = module.os.rename
    if sidecar_conflict:
        old.with_suffix('.xmp').write_text('external sidecar', encoding='utf-8')
    else:
        def deny_sidecar(source, target):
            if str(source) == str(new.with_suffix('.xmp')):
                raise PermissionError('sidecar locked')
            rename(source, target)
        monkeypatch.setattr(module.os, 'rename', deny_sidecar)
    assert not service.rollback_last_session(session)
    assert new.exists() and not old.exists()
    assert new.with_suffix('.xmp').exists()
    assert service.db.get_video_by_path(str(new)) is not None
    assert service.db.get_history_by_session(session)
    assert service._last_rollback_result['failed_count'] == 1
    if sidecar_conflict:
        assert old.with_suffix('.xmp').read_text(encoding='utf-8') == 'external sidecar'
    else:
        monkeypatch.setattr(module.os, 'rename', rename)
        assert service.rollback_last_session(session)


def test_rollback_checks_database_target_before_moving_files(service, tmp_path):
    old = tmp_path / 'clip.mp4'
    new, session = rename_video(service, old)
    service.db.delete_video(str(new))
    assert not service.rollback_last_session(session)
    assert new.exists() and new.with_suffix('.xmp').exists()
    assert not old.exists()


def test_rollback_history_cleanup_failure_compensates_and_retains_retryable_record(service, tmp_path, monkeypatch):
    old = tmp_path / 'clip.mp4'
    new, session = rename_video(service, old)
    delete = service.db.delete_rename_record
    monkeypatch.setattr(service.db, 'delete_rename_record',
        lambda *a: (_ for _ in ()).throw(OSError('history write denied')))
    assert not service.rollback_last_session(session)
    assert new.exists() and new.with_suffix('.xmp').exists()
    assert not old.exists() and not old.with_suffix('.xmp').exists()
    assert service.db.get_video_by_path(str(new)) is not None
    assert len(service.db.get_history_by_session(session)) == 1
    monkeypatch.setattr(service.db, 'delete_rename_record', delete)
    assert service.rollback_last_session(session)


def test_rollback_compensation_failure_is_explicit_and_keeps_history(service, tmp_path, monkeypatch):
    old = tmp_path / 'clip.mp4'
    new, session = rename_video(service, old)
    rename = module.os.rename
    monkeypatch.setattr(service.db, 'update_video_path',
        lambda *a, **k: (_ for _ in ()).throw(OSError('database denied')))

    def deny_compensation(source, target):
        if str(source) == str(old):
            raise PermissionError('compensation locked')
        return rename(source, target)

    monkeypatch.setattr(module.os, 'rename', deny_compensation)
    assert not service.rollback_last_session(session)
    report = service._last_rollback_result
    assert report['restored_count'] == 0 and report['failed_count'] == 1
    assert '视频补偿失败' in report['failures'][0]
    assert 'compensation locked' in report['failures'][0]
    assert service.db.get_history_by_session(session)
    assert old.exists() and not new.exists()
    assert service.db.get_video_by_path(str(new)) is not None


def test_rollback_compensation_never_overwrites_external_target(service, tmp_path, monkeypatch):
    old = tmp_path / 'clip.mp4'
    new, session = rename_video(service, old)

    def fail_with_external_file(*args, **kwargs):
        new.write_bytes(b'external file during database failure')
        raise OSError('database denied')

    monkeypatch.setattr(service.db, 'update_video_path', fail_with_external_file)
    assert not service.rollback_last_session(session)
    assert new.read_bytes() == b'external file during database failure'
    assert old.read_bytes() == b'synthetic clip.mp4'
    assert '未覆盖' in service._last_rollback_result['failures'][0]
    assert service.db.get_history_by_session(session)


def test_partial_rollback_removes_only_successful_history_and_retries_remaining(service, tmp_path, monkeypatch):
    paths = [tmp_path / 'first.mp4', tmp_path / 'second.mp4']
    for path in paths:
        add_video(service, path)
    service.set_work_scope_paths(list(map(str, paths)), scan=False)
    service.batch_rename(selected_paths=list(map(str, paths)), custom_pattern='new-{original_name}')
    session = service._last_rename_result['session_id']
    update = service.db.update_video_path

    def fail_first(source, target, **kwargs):
        if target == str(paths[0]):
            raise OSError('first database update denied')
        return update(source, target, **kwargs)

    monkeypatch.setattr(service.db, 'update_video_path', fail_first)
    assert not service.rollback_last_session(session)
    history = service.db.get_history_by_session(session)
    assert len(history) == 1 and history[0]['old_path'] == str(paths[0])
    assert paths[1].exists() and not paths[0].exists()
    # 已成功条目的新路径出现外部文件，下一次不得把它当成同一会话处理。
    external = paths[1].with_name('new-' + paths[1].name)
    external.write_bytes(b'external file after partial rollback')
    monkeypatch.setattr(service.db, 'update_video_path', update)
    assert service.rollback_last_session(session)
    assert external.read_bytes() == b'external file after partial rollback'
    assert service.get_work_scope_paths() == list(map(str, paths))


@pytest.mark.skipif(os.name != 'nt', reason='验证 Windows 大小写和斜杠等价路径')
def test_batch_rename_accepts_equivalent_windows_path_and_keeps_target_set(service, tmp_path):
    selected = tmp_path / 'MixedCase.mp4'
    outside = tmp_path / 'outside.mp4'
    add_video(service, selected)
    add_video(service, outside)
    alternate = str(selected).replace('\\', '/').upper()
    assert Path(alternate).exists() and alternate != str(selected)
    service.batch_rename(selected_paths=[alternate], custom_pattern='new-{original_name}')
    assert service._last_rename_result['success_count'] == 1
    assert selected.with_name('new-' + selected.name).exists()
    assert outside.exists() and not outside.with_name('new-outside.mp4').exists()


def test_physical_migration_remaps_only_successful_explicit_paths(service, tmp_path, monkeypatch):
    paths = [tmp_path / 'source' / name for name in ('ok.mp4', 'failed.mp4', 'blocked.mp4')]
    for path in paths:
        add_video(service, path)
    service.set_work_scope_paths(list(map(str, paths)), scan=False)
    target = tmp_path / 'organized'
    plan = service.plan_physical_migration(str(target), list(map(str, paths)))
    # 预览后的外部变化被真实执行检查阻止。
    paths[2].write_bytes(b'external changed bytes')
    move = service.db.move_video_record

    def deny_one(source, destination, reason):
        if source == str(paths[1]):
            raise OSError('database move denied')
        return move(source, destination, reason)

    monkeypatch.setattr(service.db, 'move_video_record', deny_one)
    result = service.execute_physical_migration(str(target), list(map(str, paths)), plan=plan, return_details=True)
    moved = target / 'Broll' / paths[0].name
    assert (result['success_count'], result['failed_count'], result['blocked_count']) == (1, 1, 1)
    assert service.get_work_scope_paths() == [str(moved), str(paths[1]), str(paths[2])]
    assert set(service.get_work_scope_video_paths()) == {str(moved), str(paths[1]), str(paths[2])}
    assert paths[1].exists() and paths[2].exists()


def test_physical_migration_does_not_expand_directory_scope(service, tmp_path):
    source = tmp_path / 'source'
    path = source / 'clip.mp4'
    add_video(service, path)
    service.set_work_scope_paths([str(source)], scan=False)
    assert service.execute_physical_migration(str(tmp_path / 'target'), [str(path)]) == 1
    assert service.get_work_scope_paths() == [str(source)]
    assert service.get_work_scope_video_paths() == []
