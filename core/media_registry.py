"""全库内容身份、发现记录及监控执行状态；不依赖 Qt。"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
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
        before = os.stat(path)
        digest = self.hasher(path)
        after = os.stat(path)
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise OSError('指纹校验期间文件仍在变化，等待稳定后重试')
        with self.db.lock, self.db._managed_connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute("SELECT * FROM videos WHERE path=? LIMIT 1", (path,)).fetchone()
            if row is None and os.name == 'nt':
                # 仅大小写/斜杠变体才走这条低频兼容路径；普通登记保持索引友好的精确查询。
                identity = path_key(path)
                candidates = conn.execute("SELECT id,path FROM videos ORDER BY id").fetchall()
                match = None
                for candidate in candidates:
                    candidate_path = candidate['path']
                    if not candidate_path:
                        continue
                    if path_key(candidate_path) != identity:
                        continue
                    try:
                        if os.path.samefile(candidate_path, path):
                            match = candidate
                            break
                    except OSError:
                        continue
                if match is not None:
                    row = conn.execute("SELECT * FROM videos WHERE id=?", (match['id'],)).fetchone()
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
        return self.db._row_to_video_dict(result)

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
