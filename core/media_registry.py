"""全库内容身份、发现记录及监控执行状态；不依赖 Qt。"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextvars import copy_context
from datetime import datetime, timezone

FILE_WAIT_PREFIX = '等待文件可读取：'


def utc_now():
    return datetime.now(timezone.utc).isoformat()


VIDEO_COLUMNS = {
    'content_version': 'INTEGER NOT NULL DEFAULT 1',
    'original_filename': "TEXT NOT NULL DEFAULT ''",
    'source_kind': "TEXT NOT NULL DEFAULT 'unknown'",
    'source_task_id': "TEXT NOT NULL DEFAULT ''",
    'source_batch_id': "TEXT NOT NULL DEFAULT ''",
    'review_state': "TEXT NOT NULL DEFAULT 'unreviewed'",
    'fingerprint_state': "TEXT NOT NULL DEFAULT 'pending'",
    'duplicate_of': 'INTEGER',
    'duplicate_group': "TEXT NOT NULL DEFAULT ''",
    'duplicate_original_version': 'INTEGER',
    'file_size': 'INTEGER',
    'file_mtime_ns': 'INTEGER',
}


def install_schema(connection):
    columns = {row[1] for row in connection.execute('PRAGMA table_info(videos)')}
    for name, definition in VIDEO_COLUMNS.items():
        if name not in columns:
            connection.execute(f'ALTER TABLE videos ADD COLUMN {name} {definition}')
    statements = (
        "CREATE TABLE IF NOT EXISTS media_content_groups(file_hash TEXT PRIMARY KEY,original_video_id INTEGER NOT NULL,original_version INTEGER NOT NULL)",
        "CREATE TABLE IF NOT EXISTS media_content_versions(video_id INTEGER NOT NULL,version INTEGER NOT NULL,file_hash TEXT,snapshot TEXT NOT NULL,PRIMARY KEY(video_id,version))",
        "CREATE TABLE IF NOT EXISTS media_discoveries(id INTEGER PRIMARY KEY,video_id INTEGER NOT NULL,source TEXT NOT NULL,task_id TEXT,batch_id TEXT,discovered_at TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS media_watch_state(task_id TEXT PRIMARY KEY,baseline_done INTEGER NOT NULL DEFAULT 0,paused INTEGER NOT NULL DEFAULT 0)",
        "CREATE TABLE IF NOT EXISTS media_watch_baseline(task_id TEXT NOT NULL,path_key TEXT NOT NULL,PRIMARY KEY(task_id,path_key))",
        "CREATE TABLE IF NOT EXISTS media_watch_batches(id TEXT PRIMARY KEY,task_id TEXT NOT NULL,created_at TEXT NOT NULL,state TEXT NOT NULL,config TEXT NOT NULL,snapshot TEXT NOT NULL DEFAULT '{}',rename_session_id TEXT)",
        "CREATE TABLE IF NOT EXISTS media_watch_items(id INTEGER PRIMARY KEY,batch_id TEXT NOT NULL,task_id TEXT NOT NULL,video_id INTEGER NOT NULL,content_version INTEGER NOT NULL,path TEXT NOT NULL,stage TEXT NOT NULL,error TEXT NOT NULL DEFAULT '',updated_at TEXT NOT NULL,UNIQUE(task_id,video_id,content_version))",
        'CREATE INDEX IF NOT EXISTS idx_media_watch_items_queue ON media_watch_items(stage,task_id,id)',
        'CREATE INDEX IF NOT EXISTS idx_media_watch_items_batch ON media_watch_items(batch_id,id)',
        'CREATE INDEX IF NOT EXISTS idx_videos_duplicate ON videos(duplicate_of)',
        'CREATE INDEX IF NOT EXISTS idx_videos_source_review ON videos(source_task_id,review_state,id)',
        'CREATE INDEX IF NOT EXISTS idx_media_discoveries_video ON media_discoveries(video_id,id)',
    )
    for statement in statements:
        connection.execute(statement)
    connection.execute("UPDATE videos SET original_filename=filename WHERE original_filename='' AND filename IS NOT NULL")
    if connection.execute('PRAGMA user_version').fetchone()[0] < 5:
        for row in connection.execute("SELECT file_hash FROM videos WHERE file_hash IS NOT NULL AND trim(file_hash)<>'' GROUP BY file_hash").fetchall():
            _classify(connection, row[0])


def _classify(connection, digest):
    if not digest:
        return
    members = connection.execute('SELECT id,content_version FROM videos WHERE file_hash=? ORDER BY id', (digest,)).fetchall()
    if not members:
        return
    connection.execute('INSERT OR IGNORE INTO media_content_groups VALUES(?,?,?)', (digest, members[0][0], members[0][1]))
    owner = connection.execute('SELECT original_video_id,original_version FROM media_content_groups WHERE file_hash=?', (digest,)).fetchone()
    # 离线历史素材晚于副本恢复可读时，按真实入库编号纠正首次未知的归属。
    if members[0][0] < owner[0]:
        connection.execute('UPDATE media_content_groups SET original_video_id=?,original_version=? WHERE file_hash=?', (members[0][0], members[0][1], digest))
        owner = members[0]
    connection.execute(
        'UPDATE videos SET duplicate_of=CASE WHEN id=? AND content_version=? THEN NULL ELSE ? END,'
        'duplicate_original_version=?,duplicate_group=? WHERE file_hash=?',
        (owner[0], owner[1], owner[0], owner[1], digest if len(members) > 1 or members[0][0] != owner[0] else '', digest),
    )


def full_hash(path):
    digest = hashlib.md5()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def path_key(path):
    return os.path.normcase(os.path.abspath(path)).replace('\\', '/').lower()


class MediaRegistry:
    def __init__(self, db, hasher=None):
        self.db = db
        self.hasher = hasher or full_hash
        self._index_lock = threading.RLock()
        self._indexed = False

    def index_existing(self, progress=None, *, force=False):
        """补齐历史指纹；批次边界可 force 重试仍处于 pending 的离线文件。"""
        with self._index_lock:
            if self._indexed and not force:
                return False
            rows = self.db.execute_query("SELECT id,path FROM videos WHERE fingerprint_state='pending' ORDER BY id")
            updated = False
            for index, row in enumerate(rows):
                try:
                    current = self.register(
                        row['path'], source='unknown', record_event=False, index_existing=False
                    )
                    updated = updated or bool(
                        current
                        and current.get('fingerprint_state') != 'pending'
                        and current.get('file_hash')
                    )
                except (OSError, ValueError):
                    pass
                if progress:
                    progress(index + 1, len(rows))
            self._indexed = True
            return updated

    def register(self, path, *, source='manual', task_id='', batch_id='', record_event=True, index_existing=True,
                 owned_content_update=False):
        path = os.path.abspath(path)
        if index_existing:
            self.index_existing()
        digest, after = self._prepare_fingerprint(path)
        with self.db.lock, self.db._managed_connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            self._verify_fingerprint_snapshot(path, after)
            result, _ = self._register_on_connection(
                conn, path, digest, after, source=source, task_id=task_id,
                batch_id=batch_id, record_event=record_event,
                owned_content_update=owned_content_update,
            )
        return self.db._row_to_video_dict(result)

    def _prepare_fingerprint(self, path):
        """完整读取文件，并拒绝读取过程中变化的内容。"""
        before = os.stat(path)
        digest = self.hasher(path)
        after = os.stat(path)
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise OSError('指纹校验期间文件仍在变化，等待稳定后重试')
        return digest, after

    @staticmethod
    def _verify_fingerprint_snapshot(path, observed):
        current = os.stat(path)
        if (current.st_size, current.st_mtime_ns) != (observed.st_size, observed.st_mtime_ns):
            raise OSError('指纹校验后文件发生变化，等待稳定后重试')

    @staticmethod
    def _path_index(conn):
        index = {}
        for row in conn.execute('SELECT id,path FROM videos ORDER BY id'):
            if row['path']:
                index.setdefault(path_key(row['path']), []).append((row['id'], row['path']))
        return index

    def _find_record(self, conn, path, path_index=None):
        row = conn.execute("SELECT * FROM videos WHERE path=? LIMIT 1", (path,)).fetchone()
        if row is None and os.name == 'nt':
            identity = path_key(path)
            if path_index is None:
                candidates = [(row['id'], row['path']) for row in
                              conn.execute('SELECT id,path FROM videos ORDER BY id')]
            else:
                candidates = path_index.get(identity, ())
            for video_id, candidate_path in candidates:
                if not candidate_path or path_key(candidate_path) != identity:
                    continue
                try:
                    if os.path.samefile(candidate_path, path):
                        candidate = conn.execute('SELECT * FROM videos WHERE id=?', (video_id,)).fetchone()
                        if candidate is not None and path_key(candidate['path']) == identity:
                            return candidate
                except OSError:
                    continue
        return row

    def _register_on_connection(self, conn, path, digest, after, *, source='manual',
                                task_id='', batch_id='', record_event=True,
                                owned_content_update=False, path_index=None):
        """单条和批量登记共用的持久化核心，事务归调用方所有。"""
        row = self._find_record(conn, path, path_index)
        is_new = row is None
        if row is None:
            cursor = conn.execute(
                "INSERT INTO videos(path,filename,original_filename,file_hash,status,tags,tag_groups,raw_metadata,source_kind,source_task_id,source_batch_id) VALUES(?,?,?,?,'pending','[]','{}','{}',?,?,?)",
                (path, os.path.basename(path), os.path.basename(path), digest, source, task_id, batch_id),
            )
            video_id = cursor.lastrowid
            version = 1
            state = 'verified'
        else:
            row = dict(row)
            video_id, version = row['id'], row['content_version']
            changed = bool(row.get('file_hash')) and row['file_hash'] != digest
            state = row['fingerprint_state'] if row['fingerprint_state'] == 'review' else 'verified'
            if changed:
                conn.execute('INSERT OR IGNORE INTO media_content_versions VALUES(?,?,?,?)', (video_id, version, row['file_hash'], json.dumps(row, ensure_ascii=False)))
                version += 1
                state = 'review' if not owned_content_update and (
                    row.get('manual_override') or row.get('status') in ('edited', 'processing')) else 'verified'
                if state != 'review':
                    conn.execute("UPDATE videos SET category=NULL,summary=NULL,tags='[]',tag_groups='{}',transcription=NULL,thumbnail=NULL,raw_metadata='{}',status='pending',metadata_injected=0 WHERE id=?", (video_id,))
                conn.execute("UPDATE videos SET review_state='unreviewed' WHERE id=?", (video_id,))
            conn.execute('UPDATE videos SET file_hash=?,content_version=? WHERE id=?', (digest, version, video_id))
            if changed and owned_content_update:
                # 程序自身写回只推进活动队列的内容绑定；历史批次仍保留原版本。
                conn.execute(
                    "UPDATE media_watch_items SET content_version=?,updated_at=? WHERE video_id=? AND content_version=? "
                    "AND stage IN ('queued','pending','analyzing','rename_ready','xmp_ready')",
                    (version, utc_now(), video_id, version - 1),
                )
            if changed:
                _classify(conn, row['file_hash'])
        conn.execute('UPDATE videos SET file_size=?,file_mtime_ns=?,fingerprint_state=?,is_missing=0 WHERE id=?', (after.st_size, after.st_mtime_ns, state, video_id))
        _classify(conn, digest)
        if record_event:
            conn.execute('INSERT INTO media_discoveries(video_id,source,task_id,batch_id,discovered_at) VALUES(?,?,?,?,?)', (video_id, source, task_id, batch_id, utc_now()))
        result = dict(conn.execute('SELECT * FROM videos WHERE id=?', (video_id,)).fetchone())
        if path_index is not None and is_new:
            path_index.setdefault(path_key(path), []).append((video_id, path))
        return result, is_new

    def register_many(self, paths, *, source='manual', progress=None, workers=4, batch_size=64,
                      on_batch=None):
        """有界并行完整指纹、按输入顺序批量提交，错误逐条隔离。"""
        paths = list(dict.fromkeys(os.path.abspath(path) for path in paths))
        total = len(paths)
        result = {'items': [], 'errors': []}
        if not paths:
            return result
        self.index_existing()
        workers = max(1, min(int(workers), 4))
        batch_size = max(1, min(int(batch_size), 256))
        fingerprint_done = register_done = 0

        def report(phase, done):
            if progress:
                progress(phase, done, total)

        report('fingerprint', 0)
        report('register', 0)
        committed = []
        # 复用连接避免每个文件关闭连接时重复触发WAL检查点；每批仍明确提交。
        with self.db.reuse_connection(), ThreadPoolExecutor(max_workers=workers) as pool:
            with self.db.lock, self.db._managed_connection() as conn:
                path_index = self._path_index(conn) if os.name == 'nt' else None
                data_version = conn.execute('PRAGMA data_version').fetchone()[0]
            offsets = iter(range(0, total, batch_size))
            pending = deque()

            def submit_next_batch():
                offset = next(offsets, None)
                if offset is None:
                    return
                batch = paths[offset:offset + batch_size]
                futures = {pool.submit(copy_context().run, self._prepare_fingerprint, path): path
                           for path in batch}
                pending.append((batch, futures))

            # 最多当前批和下一批，避免全目录future占用内存；DB提交时哈希继续工作。
            submit_next_batch()
            submit_next_batch()
            while pending:
                batch, futures = pending.popleft()
                prepared = {}
                for future in as_completed(futures):
                    path = futures[future]
                    try:
                        prepared[path] = future.result()
                    except (OSError, ValueError) as exc:
                        result['errors'].append({'path': path, 'reason': str(exc)})
                    fingerprint_done += 1
                    report('fingerprint', fingerprint_done)
                batch_committed = []
                batch_snapshots = []
                with self.db.lock, self.db._managed_connection() as conn:
                    conn.execute('BEGIN IMMEDIATE')
                    current_version = conn.execute('PRAGMA data_version').fetchone()[0]
                    # 其他连接若在哈希期间登记或改名，刷新一次索引以保留路径身份。
                    if path_index is not None and current_version != data_version:
                        path_index = self._path_index(conn)
                    data_version = current_version
                    for path in batch:
                        if path in prepared:
                            digest, observed = prepared[path]
                            conn.execute('SAVEPOINT media_register_item')
                            try:
                                self._verify_fingerprint_snapshot(path, observed)
                                row, is_new = self._register_on_connection(
                                    conn, path, digest, observed, source=source, path_index=path_index)
                            except Exception as exc:
                                conn.execute('ROLLBACK TO media_register_item')
                                conn.execute('RELEASE media_register_item')
                                # 失败可能发生在核心返回前，撤去索引中的未提交行。
                                if path_index is not None:
                                    path_index = self._path_index(conn)
                                result['errors'].append({'path': path, 'reason': str(exc)})
                            else:
                                conn.execute('RELEASE media_register_item')
                                batch_committed.append((row['id'], is_new))
                                batch_snapshots.append((self.db._row_to_video_dict(row), is_new))
                        register_done += 1
                    # 退出managed_connection后该批次才算成功；提交失败向上抛出。
                committed.extend(batch_committed)
                report('register', register_done)
                if on_batch:
                    on_batch(batch_snapshots)
                submit_next_batch()
            # 后来的同内容成员或早期历史记录可能纠正原记录归属，返回最终分类。
            with self.db.lock, self.db._managed_connection() as conn:
                for offset in range(0, len(committed), 256):
                    chunk = committed[offset:offset + 256]
                    ids = [item[0] for item in chunk]
                    placeholders = ','.join('?' for _ in ids)
                    rows = {row['id']: dict(row) for row in conn.execute(
                        f'SELECT * FROM videos WHERE id IN ({placeholders})', ids)}
                    for video_id, is_new in chunk:
                        if video_id in rows:
                            result['items'].append((self.db._row_to_video_dict(rows[video_id]), is_new))
        return result

    def eligible_paths(self, paths, *, verify=True, force_index=True):
        self.index_existing(force=force_index)
        eligible = []
        for path in dict.fromkeys(paths):
            if verify and os.path.isfile(path):
                row = self.register(path, record_event=False, index_existing=False)
            else:
                row = self.db.get_video_by_path(path)
            if row and row.get('file_hash'):
                with self.db.lock, self.db._managed_connection() as conn:
                    _classify(conn, row['file_hash'])
                row = self.db.get_video_by_path(path)
            if not row or (not row.get('duplicate_of') and row.get('fingerprint_state') != 'review'):
                eligible.append(path)
        return eligible

    def mark_reviewed(self, paths, reviewed=True):
        for path in paths:
            self.db.execute_non_query('UPDATE videos SET review_state=? WHERE path=?', ('reviewed' if reviewed else 'unreviewed', path))

    def accept_changes(self, paths):
        for path in paths:
            self.db.execute_non_query("UPDATE videos SET fingerprint_state='verified',manual_override=0,status='pending',category=NULL,summary=NULL,tags='[]',tag_groups='{}',transcription=NULL,thumbnail=NULL WHERE path=? AND fingerprint_state='review' AND duplicate_of IS NULL", (path,))

    def state(self, task_id):
        self.db.execute_non_query('INSERT OR IGNORE INTO media_watch_state(task_id) VALUES(?)', (task_id,))
        return self.db.execute_query('SELECT * FROM media_watch_state WHERE task_id=?', (task_id,))[0]

    def set_paused(self, task_id, paused):
        self.state(task_id)
        self.db.execute_non_query('UPDATE media_watch_state SET paused=? WHERE task_id=?', (int(paused), task_id))
        if not paused:
            self.db.execute_non_query("UPDATE media_watch_batches SET state='queued' WHERE task_id=? AND state='paused'", (task_id,))

    def enqueue(self, row, task_id, config, stage='queued'):
        with self.db.lock, self.db._managed_connection() as conn:
            previous = conn.execute('SELECT * FROM media_watch_items WHERE task_id=? AND video_id=? AND content_version=?', (task_id, row['id'], row['content_version'])).fetchone()
            if previous:
                if previous['stage'] in ('queued', 'rename_ready', 'xmp_ready', 'pending') and previous['error'].startswith(FILE_WAIT_PREFIX):
                    resumed = conn.execute(
                        "UPDATE media_watch_batches SET state='queued' WHERE id=? AND state IN ('paused','failed')",
                        (previous['batch_id'],),
                    )
                    if resumed.rowcount:
                        conn.execute("UPDATE media_watch_items SET error='',updated_at=? WHERE id=?", (utc_now(), previous['id']))
                conn.execute("UPDATE media_discoveries SET batch_id=? WHERE id=(SELECT MAX(id) FROM media_discoveries WHERE video_id=? AND task_id=?) AND batch_id=''", (previous['batch_id'], row['id'], task_id))
                return dict(previous)
            owned = conn.execute('SELECT id FROM media_watch_items WHERE video_id=? AND content_version=? LIMIT 1', (row['id'], row['content_version'])).fetchone()
            if owned and stage not in ('duplicate', 'review'):
                stage = 'done'
            batch = conn.execute("SELECT id FROM media_watch_batches WHERE task_id=? AND state='queued' AND config=? AND (SELECT COUNT(*) FROM media_watch_items WHERE batch_id=media_watch_batches.id)<? ORDER BY created_at LIMIT 1", (task_id, json.dumps(config, sort_keys=True), config.get('batch_size', 100))).fetchone()
            batch_id = batch[0] if batch else uuid.uuid4().hex
            if not batch:
                conn.execute('INSERT INTO media_watch_batches(id,task_id,created_at,state,config) VALUES(?,?,?,\'queued\',?)', (batch_id, task_id, utc_now(), json.dumps(config, sort_keys=True)))
            conn.execute('INSERT INTO media_watch_items(batch_id,task_id,video_id,content_version,path,stage,updated_at) VALUES(?,?,?,?,?,?,?)', (batch_id, task_id, row['id'], row['content_version'], row['path'], stage, utc_now()))
            conn.execute("UPDATE media_discoveries SET batch_id=? WHERE id=(SELECT MAX(id) FROM media_discoveries WHERE video_id=? AND task_id=?) AND batch_id=''", (batch_id, row['id'], task_id))
            if row.get('source_kind') == 'watch' and not row.get('source_batch_id'):
                conn.execute('UPDATE videos SET source_batch_id=? WHERE id=?', (batch_id, row['id']))
            return dict(conn.execute('SELECT * FROM media_watch_items WHERE task_id=? AND video_id=? AND content_version=?', (task_id, row['id'], row['content_version'])).fetchone())

    def update_item(self, item_id, stage, error=''):
        self.db.execute_non_query('UPDATE media_watch_items SET stage=?,error=?,updated_at=? WHERE id=?', (stage, error, utc_now(), item_id))

    def has_waiting_file(self, task_id, path):
        """精确查找可恢复的文件等待项，不依赖界面最近一页的记录。"""
        with self.db.lock, self.db._managed_connection() as conn:
            # SQLite 内置 lower 仅转换 ASCII；复用登记时的路径规则以覆盖
            # Windows Unicode 大小写、分隔符及绝对路径的等价形式。
            conn.create_function('media_watch_path_key', 1, path_key, deterministic=True)
            return conn.execute(
                "SELECT 1 FROM media_watch_items WHERE task_id=? "
                "AND stage IN ('queued','rename_ready','xmp_ready','pending') "
                "AND substr(error,1,?)=? AND media_watch_path_key(path)=? LIMIT 1",
                (task_id, len(FILE_WAIT_PREFIX), FILE_WAIT_PREFIX, path_key(path)),
            ).fetchone() is not None

    def items(self, task_id='', limit=200, *, offset=0, stage=''):
        conditions, params = [], []
        for field, value in [('task_id', task_id), ('stage', stage)]:
            if value:
                conditions.append('i.' + field + '=?')
                params.append(value)
        condition = ' WHERE ' + ' AND '.join(conditions) if conditions else ''
        return self.db.execute_query('SELECT i.*,v.filename,v.duplicate_of,v.duplicate_group,v.fingerprint_state,v.review_state,v.source_kind,v.source_task_id,v.source_batch_id FROM media_watch_items i JOIN videos v ON v.id=i.video_id' + condition + ' ORDER BY i.id DESC LIMIT ? OFFSET ?', tuple(params) + (limit, offset))

    def reset_items(self, ids, stage='queued'):
        for item_id in ids:
            self.update_item(item_id, stage)
            self.db.execute_non_query("UPDATE media_watch_batches SET state='queued' WHERE id=(SELECT batch_id FROM media_watch_items WHERE id=?)", (item_id,))


def management_label(row, tasks=None):
    parts = []
    if row.get('duplicate_of'):
        parts.append(f"重复副本 → #{row['duplicate_of']}（跳过处理）")
    elif row.get('duplicate_group'):
        parts.append('重复组原记录')
    if row.get('fingerprint_state') == 'review':
        parts.append('内容变化待核对')
    elif row.get('fingerprint_state') == 'pending':
        parts.append('指纹待校验')
    if row.get('source_kind') == 'watch':
        name = (tasks or {}).get(row.get('source_task_id'), row.get('source_task_id', ''))
        parts.append('监控来源' + (f'：{name}' if name else ''))
    elif row.get('source_kind') == 'manual':
        parts.append('手动入库')
    elif row.get('source_kind') == 'unknown':
        parts.append('历史来源未知')
    if row.get('review_state') or row.get('source_kind') or row.get('duplicate_group'):
        parts.append('已核对' if row.get('review_state') == 'reviewed' else '未核对')
    return ' · '.join(parts)
