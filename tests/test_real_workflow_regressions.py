"""真实工作流反馈的最小回归；只操作临时合成素材。"""
import pytest
from tests.support.failure_center import service as service

def test_rename_preserves_explicit_scope_and_rollback(service, tmp_path):
    path = tmp_path / 'clip.mp4'
    path.write_bytes(b'synthetic')
    service.db.upsert_video({'path': str(path), 'filename': path.name, 'status': 'analyzed', 'summary': ''})
    service.set_work_scope_paths([str(path)], scan=False)
    service.batch_rename(selected_paths=[str(path)], custom_pattern='new-{original_name}')
    target = str(tmp_path / 'new-clip.mp4')
    assert service.get_work_scope_video_paths() == [target]
    assert service.rollback_last_session()
    assert service.get_work_scope_video_paths() == [str(path)]

def test_large_explicit_scope_can_query(service, tmp_path):
    paths = [str(tmp_path / f'{i}.mp4') for i in range(1400)]
    outside = str(tmp_path / 'outside.mp4')
    service.db.insert_videos_if_absent([{'path': p, 'filename': str(i), 'status': 'pending'} for i, p in enumerate([*paths, outside])])
    service.set_work_scope_paths(paths, scan=False)
    actual = service.get_work_scope_video_paths()
    assert len(actual) == len(paths)
    assert set(actual) == set(paths)

@pytest.mark.parametrize('batch', [False, True])
def test_updates_do_not_consume_future_library_ids(service, tmp_path, batch):
    rows = [{'path': str(tmp_path / f'{i}.mp4'), 'filename': f'{i}.mp4', 'status': 'pending'} for i in range(40)]
    service.db.insert_videos_if_absent(rows)
    for row in rows:
        row['status'] = 'analyzed'
    if batch:
        service.db.upsert_videos_batch(rows)
    else:
        for row in rows:
            service.db.upsert_video(row)
    service.db.insert_videos_if_absent([{'path': str(tmp_path / 'next.mp4'), 'filename': 'next.mp4', 'status': 'pending'}])
    assert service.db.get_video_by_path(str(tmp_path / 'next.mp4'))['id'] == 41

def test_import_analyze_rename_rollback_then_import_keeps_ids(service, tmp_path):
    from core.media_registry import full_hash
    # 本用例包含改名，内容身份必须使用真实字节而非共享替身的路径字符串。
    service.media_registry.hasher = full_hash
    paths = []
    for i in range(80):
        path = tmp_path / f'{i:04}.mp4'
        path.write_bytes(f'synthetic-{i}'.encode())
        paths.append(str(path))
    # 入库缩略图回写同样经过真实upsert路径，不需要媒体解码或外网请求。
    service.processor.extract_frames = lambda *a, **kw: {'thumbnail': str(tmp_path / 'synthetic.webp')}
    service.set_work_scope_paths(paths[:40])
    for path in paths[:40]:
        row = service.db.get_video_by_path(path)
        service.db.upsert_video({**row, 'status': 'analyzed', 'summary': '合成分析结果'})
    service.batch_rename(selected_paths=paths[:40], custom_pattern='new-{original_name}')
    assert service.rollback_last_session()
    service.append_work_scope(paths[40:])
    service.scan_and_register_work_scope()
    rows = service.db.get_all_videos()
    assert len(rows) == 80
    assert sorted(row['id'] for row in rows) == list(range(1, 81))
    assert sorted(service.get_work_scope_video_paths()) == sorted(paths)
    assert all(service.db.get_video_by_path(p)['status'] == 'analyzed' for p in paths[:40])

def test_deleted_library_ids_are_not_reused(service, tmp_path):
    first, second = str(tmp_path / 'first.mp4'), str(tmp_path / 'second.mp4')
    service.db.upsert_video({'path': first, 'status': 'pending'})
    service.db.delete_video(first)
    service.db.upsert_video({'path': second, 'status': 'pending'})
    assert service.db.get_video_by_path(second)['id'] == 2

def test_main_table_status_is_chinese_and_not_assumed_400():
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import Qt
    from gui.models.video_table import VideoTableModel, COL_STATUS
    app = QApplication.instance() or QApplication([])
    model = VideoTableModel()
    model.update_data([{'path': 'a.mp4', 'status': 'analyzed'}, {'path': 'b.mp4', 'status': 'analyzed', 'recovery_state': 'recovered'}])
    assert model.data(model.index(0, COL_STATUS), Qt.DisplayRole) == '已分析'
    assert '400' not in model.data(model.index(1, COL_STATUS), Qt.DisplayRole)

def test_ever400_checkbox_includes_recovered_by_default(service):
    from PySide6.QtWidgets import QApplication
    from gui.widgets.recovery_controls import AnalysisFailureCenterDialog
    app = QApplication.instance() or QApplication([])
    dialog = AnalysisFailureCenterDialog(service)
    dialog.ever400.setChecked(True)
    assert dialog._query().state == ''
    dialog.close()

def test_retry_completion_refreshes_parent_and_clears_stale_status(service, monkeypatch):
    from PySide6.QtWidgets import QApplication, QWidget, QMessageBox
    from gui.widgets.recovery_controls import AnalysisFailureCenterDialog
    from types import SimpleNamespace
    app = QApplication.instance() or QApplication([])
    refreshed = []
    parent = QWidget()
    parent.model = SimpleNamespace(clear_temp_status_map=lambda: refreshed.append('clear'))
    parent.load_data = lambda: refreshed.append('load')
    dialog = AnalysisFailureCenterDialog(service, parent=parent)
    monkeypatch.setattr(QMessageBox, 'information', lambda *a: None)
    monkeypatch.setattr(service, 'invalidate_video_memory_cache', lambda: refreshed.append('invalidate'))
    dialog._retry_finished(True, '已完成')
    assert refreshed == ['invalidate', 'clear', 'load']
    dialog.close()

def test_400_history_survives_changed_last_error(service, tmp_path):
    import json
    from core.analysis_failures import refresh_index, FailureQuery
    path = str(tmp_path / 'history.mp4')
    service.db.upsert_video({'path': path, 'filename': 'history.mp4', 'status': 'analyzed'})
    with service.db.lock, service.db._managed_connection() as conn:
        vid = conn.execute('SELECT id FROM videos WHERE path=?', (path,)).fetchone()[0]
        conn.execute("INSERT INTO video_recovery VALUES (?,'recovered','history','2026-11-09',?)", (vid, json.dumps({'last_error': {'status_code': 503, 'message': 'unavailable'}})))
        conn.execute("INSERT INTO video_recovery_rounds VALUES ('history',?,'2026-11-09','2026-11-09','manual','{}')", (vid,))
        conn.execute("INSERT INTO video_recovery_attempts(round_id,created_at,detail) VALUES ('history','2026-11-09',?)", (json.dumps({'status_code': 400}),))
        refresh_index(conn, vid)
    result = service.query_analysis_failures(FailureQuery(state='', ever400=True))
    assert result['total'] == 1
    assert result['rows'][0]['state'] == 'recovered'
