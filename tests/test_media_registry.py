import threading
from concurrent.futures import ThreadPoolExecutor

from core.media_registry import MediaRegistry
from core.video_organizer_service import DatabaseManager


def test_global_duplicates_keep_separate_rows_and_first_owner(tmp_path):
    db = DatabaseManager(str(tmp_path / 'catalog.db'))
    registry = MediaRegistry(db)
    first, second = tmp_path / 'a.mp4', tmp_path / 'other' / 'b.mp4'
    second.parent.mkdir()
    first.write_bytes(b'same full video')
    second.write_bytes(first.read_bytes())
    original = registry.register(str(first), source='manual')
    db.execute_non_query("UPDATE videos SET status='failed',is_missing=1 WHERE id=?", (original['id'],))
    copy = registry.register(str(second), source='watch', task_id='watch-1')
    assert copy['id'] != original['id']
    assert copy['duplicate_of'] == original['id']
    assert copy['tags'] == [] and not copy['summary']
    assert registry.eligible_paths([str(second)]) == []
    assert len(db.get_all_videos()) == 2
    registry.register(str(second), source='watch', task_id='watch-1')
    assert len(db.get_all_videos()) == 2
    assert db.execute_query('SELECT COUNT(*) AS n FROM media_discoveries')[0]['n'] == 3


def test_concurrent_discovery_has_one_owner(tmp_path):
    db = DatabaseManager(str(tmp_path / 'catalog.db'))
    registry = MediaRegistry(db)
    paths = []
    for i in range(8):
        path = tmp_path / f'{i}.mp4'
        path.write_bytes(b'identical')
        paths.append(str(path))
    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(registry.register, paths))
    assert len({row['id'] for row in rows}) == 8
    owners = [row for row in db.get_all_videos() if not row['duplicate_of']]
    assert len(owners) == 1
    assert all(row['duplicate_of'] == owners[0]['id'] for row in db.get_all_videos() if row['id'] != owners[0]['id'])


def test_changed_content_preserves_identity_and_protects_manual_edits(tmp_path):
    db = DatabaseManager(str(tmp_path / 'catalog.db'))
    registry = MediaRegistry(db)
    path = tmp_path / 'a.mp4'
    path.write_bytes(b'old')
    old = registry.register(str(path))
    db.execute_non_query("UPDATE videos SET manual_override=1,summary='human',status='edited' WHERE id=?", (old['id'],))
    path.write_bytes(b'replaced video')
    row = registry.register(str(path))
    assert row['id'] == old['id']
    assert row['content_version'] == 2
    assert row['summary'] == 'human'
    assert row['fingerprint_state'] == 'review'
    assert registry.eligible_paths([str(path)]) == []


def test_history_hash_backfill_and_missing_file_stay_explicit(tmp_path):
    db = DatabaseManager(str(tmp_path / 'catalog.db'))
    path = tmp_path / 'old.mp4'
    path.write_bytes(b'old content')
    db.insert_video_if_absent({'path': str(path), 'filename': path.name, 'status': 'pending'})
    db.insert_video_if_absent({'path': str(tmp_path / 'missing.mp4'), 'status': 'pending'})
    copy = tmp_path / 'copy.mp4'
    copy.write_bytes(path.read_bytes())
    registry = MediaRegistry(db)
    row = registry.register(str(copy))
    assert row['duplicate_of'] == db.get_video_by_path(str(path))['id']
    assert db.get_video_by_path(str(tmp_path / 'missing.mp4'))['fingerprint_state'] == 'pending'


def test_offline_history_can_be_rechecked_and_corrects_earliest_identity(tmp_path):
    db = DatabaseManager(str(tmp_path / 'catalog.db'))
    old = tmp_path / 'offline.mp4'
    db.insert_video_if_absent({'path': str(old), 'filename': old.name, 'summary': 'historical', 'status': 'analyzed'})
    registry = MediaRegistry(db)
    copy = tmp_path / 'copy.mp4'
    copy.write_bytes(b'same')
    registry.register(str(copy))
    assert db.get_video_by_path(str(old))['fingerprint_state'] == 'pending'
    old.write_bytes(copy.read_bytes())
    registry.index_existing(force=True)
    original = db.get_video_by_path(str(old))
    assert original['summary'] == 'historical'
    assert not original['duplicate_of']
    assert db.get_video_by_path(str(copy))['duplicate_of'] == original['id']
