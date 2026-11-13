"""后台监控素材目录并将稳定视频交给现有素材库和分析流程。"""
from __future__ import annotations

import logging
import os
import threading
import time
import sys
import json
import copy
from contextlib import nullcontext
from core.watch_config import normalize_watch_config, effective_directory_options, is_path_watchable
from core.media_registry import MediaRegistry, path_key, utc_now, FILE_WAIT_PREFIX
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional, Tuple

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from core.video_organizer_service import WORK_SCOPE_VIDEO_EXTENSIONS

logger = logging.getLogger("VideoOrganizer.media_watch")
STABILITY_SECONDS = 30.0
CHECK_INTERVAL_SECONDS = 5.0
RESCAN_INTERVAL_SECONDS = 300.0


def _path_key(path: str) -> str:
    return os.path.normcase(os.path.abspath(path)).replace("\\", "/").lower()


def should_start_with_windows(config: Optional[Dict[str, Any]]) -> bool:
    """仅在用户启用登录启动且至少有一个有效监控目录时注册自启。"""
    normalized = normalize_watch_config(config)
    return normalized["start_with_windows"] and any(
        directory["enabled"] for directory in normalized["directories"]
    )


def set_windows_startup(enabled: bool, executable: Optional[str] = None) -> None:
    if os.name != "nt":
        return
    import winreg
    command = executable or sys.executable
    if getattr(sys, "frozen", False):
        value = f'"{command}" --background'
    else:
        script = os.path.abspath(sys.argv[0])
        value = f'"{command}" "{script}" --background'
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, "VideoOrganizerPro", 0, winreg.REG_SZ, value)
        else:
            try:
                winreg.DeleteValue(key, "VideoOrganizerPro")
            except FileNotFoundError:
                pass


def windows_startup_enabled() -> bool:
    if os.name != "nt":
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
            winreg.QueryValueEx(key, "VideoOrganizerPro")
            return True
    except OSError:
        return False


class _EventHandler(FileSystemEventHandler):
    def __init__(self, mark_changed: Callable[[str], None], remember_move_source: Callable[[str], None]):
        super().__init__()
        self.mark_changed = mark_changed
        self.remember_move_source = remember_move_source

    def on_created(self, event):
        if not event.is_directory:
            self.mark_changed(event.src_path)

    def on_modified(self, event):
        if not event.is_directory:
            self.mark_changed(event.src_path)

    def on_moved(self, event):
        if not event.is_directory:
            self.remember_move_source(event.src_path)
            self.mark_changed(event.dest_path)


class MediaDirectoryMonitor:
    """以文件事件为主、周期扫描兜底的可停止监控器。"""

    def __init__(self, service, config: Dict[str, Any], on_status: Optional[Callable[[Dict], None]] = None):
        self.service = service
        self.config = normalize_watch_config(config)
        self.on_status = on_status or (lambda _status: None)
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._lock = threading.RLock()
        self._pending: Dict[str, Tuple[float, Optional[Tuple[int, int]]]] = {}
        self._move_candidates: Dict[str, str] = {}
        self._states: Dict[str, str] = {}
        self._observer: Optional[Observer] = None
        self._thread: Optional[threading.Thread] = None
        self._analysis_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="media-watch-analysis")
        self._analysis_lock = threading.Lock()
        self._analysis_active = False
        self._analysis_active_path = ""
        self._analysis_queue: List[str] = []
        self._analysis_shutdown = False
        self.registry = getattr(service, 'media_registry', None)
        if self.registry is None and hasattr(service.db, '_managed_connection'):
            self.registry = MediaRegistry(service.db, hasher=getattr(getattr(service, 'processor', None), 'get_file_hash', None))
        self._paused_all = False
        self._cancel_batch = threading.Event()
        self._batch_pause_requested = threading.Event()
        self._scan_requested = False
        self._historical_tasks = set()
        self._active_batch_id = ''
        self._last_snapshot = {}
        self._discovery = {}
        self._pending_paths = {}
        self._last_status_emit = 0.0
        if self.registry:
            # 已持久化的分析结果直接续接；请求正在执行时崩溃无法确认服务端结果，交由人工重试。
            service.db.execute_non_query("UPDATE media_watch_items SET stage=CASE WHEN (SELECT status FROM videos WHERE id=video_id) IN ('analyzed','renamed') THEN 'rename_ready' ELSE 'review' END,error='重启恢复：请核对中断阶段' WHERE stage='analyzing'")
            service.db.execute_non_query("UPDATE media_watch_items SET stage='xmp_ready' WHERE stage='xmp'")
            service.db.execute_non_query("UPDATE media_watch_items SET stage=CASE WHEN EXISTS(SELECT 1 FROM rename_history h WHERE h.session_id=media_watch_items.batch_id AND h.new_path=media_watch_items.path) THEN 'xmp_ready' ELSE 'review' END,error='重启恢复：请核对重命名结果' WHERE stage='renaming'")
            service.db.execute_non_query("UPDATE media_watch_batches SET state='queued' WHERE state='running'")

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive() and not self._stop.is_set() and not self._paused_all

    @property
    def analysis_running(self) -> bool:
        with self._analysis_lock:
            return self._analysis_active or bool(self._analysis_queue)

    def start(self) -> bool:
        roots = [item for item in self.config["directories"] if item["enabled"]]
        if not roots:
            return False
        self._paused_all = False
        if self._thread is not None and self._thread.is_alive():
            self._wake.set()
            return True
        self._stop.clear()
        observer = Observer()
        handler = _EventHandler(self.notify_path, self.remember_move_source)
        scheduled = False
        for item in roots:
            if not os.path.isdir(item["path"]):
                continue
            options = effective_directory_options(self.config, item["path"])
            try:
                observer.schedule(handler, item["path"], recursive=bool(options["recursive"]))
                scheduled = True
            except Exception as exc:
                logger.warning("无法监听素材目录 %s: %s", item["path"], exc)
        self._observer = observer if scheduled else None
        if self._observer is not None:
            self._observer.start()
        self._thread = threading.Thread(target=self._run, name="media-directory-monitor", daemon=True)
        self._thread.start()
        return True

    def reconfigure(self, config: Dict[str, Any]) -> None:
        self.stop()
        self.config = normalize_watch_config(config)
        self._stop = threading.Event()
        self._wake = threading.Event()
        self.start()

    def stop(self, *, wait: bool = True, shutdown_analysis: bool = False) -> None:
        self._stop.set()
        self._wake.set()
        observer = self._observer
        if observer is not None:
            observer.stop()
        if wait and self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=10)
        if observer is not None and wait:
            observer.join(timeout=10)
        if shutdown_analysis:
            self._cancel_batch.set()
            with self._analysis_lock:
                self._analysis_shutdown = True
                self._analysis_queue.clear()
            self._analysis_pool.shutdown(wait=wait, cancel_futures=True)

    def pause(self) -> None:
        self._paused_all = True
        with self._analysis_lock:
            self._batch_pause_requested.set()
            self._cancel_batch.set()
        if self.registry:
            for directory in self.config['directories']:
                self.registry.set_paused(directory['id'], True)
        self._wake.set()

    def notify_path(self, path: str) -> None:
        if not path or os.path.splitext(path)[1].lower() not in WORK_SCOPE_VIDEO_EXTENSIONS:
            return
        absolute = os.path.abspath(path)
        if not is_path_watchable(self.config, absolute):
            return
        existing = self.service.db.get_video_by_path(absolute)
        if existing and existing.get('is_missing') and os.path.isfile(absolute):
            self._set_video_missing(absolute, False)
        try:
            stat = os.stat(absolute)
            signature = (stat.st_size, stat.st_mtime_ns)
        except OSError:
            signature = None
        with self._lock:
            previous = self._pending.get(_path_key(absolute))
            if previous and previous[1] == signature:
                return
            self._pending[_path_key(absolute)] = (time.monotonic(), signature)
            self._pending_paths[_path_key(absolute)] = absolute
        self._set_state(absolute, "等待写入")
        self._wake.set()

    def remember_move_source(self, path: str) -> None:
        if (not path or os.path.splitext(path)[1].lower() not in WORK_SCOPE_VIDEO_EXTENSIONS
                or os.path.exists(path)):
            return
        absolute = os.path.abspath(path)
        if not is_path_watchable(self.config, absolute):
            return
        existing = self.service.db.get_video_by_path(absolute)
        if existing:
            stored_path = existing.get("path") or absolute
            with self._lock:
                self._move_candidates[_path_key(stored_path)] = stored_path

    def statuses(self) -> List[Dict[str, str]]:
        with self._lock:
            return [{"path": path, "status": status} for path, status in self._states.items()]

    def _set_state(self, path: str, status: str) -> None:
        with self._lock:
            self._states[os.path.abspath(path)] = status
        try:
            if time.monotonic() - self._last_status_emit < .2:
                return
            self._last_status_emit = time.monotonic()
            self.on_status({"path": os.path.abspath(path), "status": status})
        except Exception:
            logger.debug("监控状态回调失败", exc_info=True)

    def _set_video_missing(self, path: str, missing: bool) -> None:
        setter = getattr(self.service.db, "set_video_missing", None)
        if callable(setter):
            changed = setter(path, missing)
        else:
            row = self.service.db.get_video_by_path(path)
            if not row:
                return
            stored_path = row.get("path") or path
            self.service.db.execute_non_query(
                "UPDATE videos SET is_missing=? WHERE path=?", (int(missing), stored_path)
            )
            changed = True
        if changed:
            invalidate = getattr(self.service, "invalidate_video_memory_cache", None)
            if callable(invalidate):
                invalidate()

    def _scan_root(self, root: Dict[str, Any]) -> Tuple[bool, List[str]]:
        path = root["path"]
        if not os.path.isdir(path):
            self._set_state(path, "目录不可用")
            return False, []
        options = effective_directory_options(self.config, path)
        recursive = bool(options["recursive"])
        found: List[str] = []
        errors: List[str] = []
        def on_error(exc):
            errors.append(str(exc))
        try:
            if recursive:
                for current, _dirs, files in os.walk(path, onerror=on_error):
                    options = effective_directory_options(self.config, current)
                    if options is None or not options["recursive"]:
                        _dirs[:] = []
                    for filename in files:
                        candidate = os.path.abspath(os.path.join(current, filename))
                        if (is_path_watchable(self.config, candidate)
                                and os.path.splitext(filename)[1].lower() in WORK_SCOPE_VIDEO_EXTENSIONS):
                            found.append(candidate)
            else:
                with os.scandir(path) as entries:
                    for entry in entries:
                        if (entry.is_file() and is_path_watchable(self.config, entry.path)
                                and os.path.splitext(entry.name)[1].lower() in WORK_SCOPE_VIDEO_EXTENSIONS):
                            found.append(os.path.abspath(entry.path))
        except OSError as exc:
            errors.append(str(exc))
        if errors:
            logger.warning("素材目录扫描不完整 %s: %s", path, errors[:3])
            return False, found
        return True, found

    def scan_now(self, task_id="", include_history=False) -> None:
        with getattr(self.service, '_file_operation_lock', nullcontext()):
            return self._scan_now(task_id, include_history)

    def _scan_now(self, task_id='', include_history=False):
        for root in self.config["directories"]:
            if not root["enabled"] or (task_id and root["id"] != task_id):
                continue
            complete, found = self._scan_root(root)
            self._discovery.setdefault(root['id'], {})['scanned'] = len(found)
            if not complete:
                continue
            options = effective_directory_options(self.config, root["path"])
            nested_roots = [
                item["path"] for item in self.config["directories"]
                if _path_key(item["path"]).rstrip("/").startswith(_path_key(root["path"]).rstrip("/") + "/")
            ]
            root_found = []
            for path in found:
                root_found.append(path)
            missing_changed = self.service.db.reconcile_monitored_paths(
                root["path"], root_found, recursive=options["recursive"],
                excluded_roots=nested_roots,
                path_filter=lambda path: is_path_watchable(self.config, path),
            )
            if missing_changed:
                invalidate = getattr(self.service, "invalidate_video_memory_cache", None)
                if callable(invalidate):
                    invalidate()
            if self.registry:
                state = self.registry.state(root['id'])
                if not state['baseline_done']:
                    if options['initial_scan_mode'] == 'new' and not include_history:
                        with self.service.db.lock, self.service.db._managed_connection() as conn:
                            conn.executemany('INSERT OR IGNORE INTO media_watch_baseline VALUES(?,?)', [(root['id'], path_key(p)) for p in found])
                    self.service.db.execute_non_query('UPDATE media_watch_state SET baseline_done=1 WHERE task_id=?', (root['id'],))
                if include_history:
                    self.service.db.execute_non_query('DELETE FROM media_watch_baseline WHERE task_id=?', (root['id'],))
                baseline = {r['path_key'] for r in self.service.db.execute_query('SELECT path_key FROM media_watch_baseline WHERE task_id=?', (root['id'],))}
            else:
                baseline = set()
            for path in root_found:
                existing = self.service.db.get_video_by_path(path)
                retry_waiting = bool(existing and self.registry and any(
                    item['path'] == path and item['error'].startswith(FILE_WAIT_PREFIX)
                    for item in self.registry.items(root['id'])
                ))
                if path_key(path) in baseline and not retry_waiting:
                    continue
                try:
                    stat = os.stat(path)
                except OSError:
                    continue
                if (existing and not retry_waiting and existing.get('file_size') == stat.st_size
                        and existing.get('file_mtime_ns') == stat.st_mtime_ns
                        and existing.get('fingerprint_state') != 'pending'):
                    self._set_state(path, self._record_state(existing))
                    continue
                self.notify_path(path)
            self._set_state(root['path'], f'扫描完成 · 发现 {len(found)} 项')

    @staticmethod
    def _record_state(row: Dict[str, Any]) -> str:
        if row.get('duplicate_of'):
            return '重复跳过'
        if row.get('fingerprint_state') == 'review':
            return '内容变化待核对'
        if row.get("is_missing"):
            return "缺失"
        return {
            "pending": "等待分析",
            "processing": "分析中",
            "failed": "失败",
            "analyzed": "成功",
            "renamed": "成功",
            "edited": "成功",
        }.get(row.get("status"), "等待分析")

    def _run(self) -> None:
        next_scans = {}
        while not self._stop.is_set():
            now = time.monotonic()
            try:
                if self._scan_requested:
                    next_scans.clear()
                    self._scan_requested = False
                for root in self.config['directories']:
                    options = effective_directory_options(self.config, root['path'])
                    if options and now >= next_scans.get(root['id'], 0):
                        self.scan_now(root['id'])
                        next_scans[root['id']] = now + options['rescan_interval']
                for task_id in list(self._historical_tasks):
                    self.scan_now(task_id, include_history=True)
                    self._historical_tasks.discard(task_id)
            except Exception:
                logger.exception('素材目录补扫失败')
            try:
                if not self._paused_all:
                    self._process_stable_files(now)
                    self._dispatch_batch()
            except Exception:
                logger.exception("检查稳定视频失败")
            intervals = [self.config['check_interval']] + [options['check_interval'] for root in self.config['directories']
                if (options := effective_directory_options(self.config, root['path']))]
            self._wake.wait(min(intervals))
            self._wake.clear()

    def _process_stable_files(self, now: float) -> None:
        ready = []
        with self._lock:
            entries = list(self._pending.items())
        for key, (changed_at, previous) in entries:
            path = self._pending_paths.get(key) or next((p for p in self._states if _path_key(p) == key), None)
            if path is None:
                continue
            options = effective_directory_options(self.config, path)
            if not options or not is_path_watchable(self.config, path):
                with self._lock:
                    self._pending.pop(key, None)
                    self._pending_paths.pop(key, None)
                continue
            if self.registry and self.registry.state(options['directory']['id'])['paused']:
                continue
            stability = options['stability_seconds'] if options else STABILITY_SECONDS
            if now - changed_at < stability:
                continue
            try:
                stat = os.stat(path)
                signature = (stat.st_size, stat.st_mtime_ns)
            except OSError:
                continue
            if previous != signature:
                with self._lock:
                    self._pending[key] = (now, signature)
                continue
            ready.append((key, path))
        for key, path in ready:
            with self._lock:
                self._pending.pop(key, None)
            self._process_video(path)

    def _process_video(self, path: str) -> None:
        with getattr(self.service, '_file_operation_lock', nullcontext()):
            return self._register_video(path)

    def _register_video(self, path):
        options = effective_directory_options(self.config, path)
        if not options or not is_path_watchable(self.config, path) or not os.path.isfile(path) or not self.registry:
            return
        try:
            self._set_state(path, '指纹校验')
            root = options['directory']
            stats = self._discovery.setdefault(root['id'], {})
            retry_waiting = any(
                item['path'] == path and item['error'].startswith(FILE_WAIT_PREFIX)
                for item in self.registry.items(root['id'])
            )
            if (not retry_waiting and self.service.db.execute_query(
                    'SELECT 1 FROM media_watch_baseline WHERE task_id=? AND path_key=?',
                    (root['id'], path_key(path))
            )):
                self._set_state(path, '历史素材已跳过')
                return
            row = self.registry.register(path, source='watch', task_id=root['id'])
            stats['verified'] = stats.get('verified', 0) + 1
            self.service.invalidate_video_memory_cache()
            stage = 'duplicate' if row.get('duplicate_of') else 'review' if row['fingerprint_state'] == 'review' else 'queued'
            if stage == 'queued' and not options['auto_analyze']:
                stage = 'pending'
            frozen = copy.deepcopy(options)
            processing = getattr(self.service, 'settings', {}).get('processing', {})
            frozen['max_workers'] = min(int(frozen['max_workers'] or processing.get('max_workers', 16)), int(processing.get('max_workers', 16)))
            frozen['analysis_retry'] = copy.deepcopy(processing.get('analysis_retry', {}))
            if frozen['xmp_mode'] == 'inherit':
                frozen['xmp_mode'] = 'on' if processing.get('auto_sync_xmp_after_analysis') else 'off'
            frozen['request_interval'] = frozen['request_interval'] if frozen['request_interval'] is not None else processing.get('request_interval', 0)
            # 固定命名方案内容；批次使用创建日，避免跨午夜预览和执行不一致。
            if frozen['rename_preset']:
                frozen['rename_pattern'] = getattr(self.service, 'tag_config', {}).get('global_settings', {}).get('presets', {}).get(frozen['rename_preset'], {}).get('filename_pattern') or frozen['rename_pattern']
            if not frozen['rename_pattern']:
                frozen['rename_pattern'] = getattr(self.service, 'tag_config', {}).get('global_settings', {}).get('export_schemes', {}).get('filename_pattern') or getattr(self.service, 'settings', {}).get('rename_pattern') or '{category}-{tags}-{summary}-{original_name}'
            self.registry.enqueue(row, root['id'], frozen, stage)
            self._set_state(path, self._record_state(row))
        except OSError as exc:
            self._set_state(path, '等待写入：' + str(exc))
            self.notify_path(path)
        except Exception:
            logger.exception('处理监控视频失败: %s', path)
            self._set_state(path, '失败')

    def request_scan(self, task_id='', include_history=False):
        if include_history:
            self._historical_tasks.add(task_id)
        self._scan_requested = True
        self._wake.set()

    def pause_task(self, task_id, paused=True):
        if self.registry:
            self.registry.set_paused(task_id, paused)
            if paused:
                with self._analysis_lock:
                    active = self.service.db.execute_query('SELECT task_id FROM media_watch_batches WHERE id=?', (self._active_batch_id,))
                    if active and active[0]['task_id'] == task_id:
                        self._batch_pause_requested.set()
                        self._cancel_batch.set()
        self._wake.set()

    def resume(self):
        self._paused_all = False
        if self.registry:
            for directory in self.config['directories']:
                self.registry.set_paused(directory['id'], False)
        self.start()

    def cancel_current_batch(self):
        with self._analysis_lock:
            self._batch_pause_requested.clear()
            self._cancel_batch.set()

    def snapshot(self, task_id=''):
        result = dict(self._last_snapshot)
        stats = dict(self._discovery.get(task_id, {}))
        with self._lock:
            pending = list(self._pending)
        stats['waiting'] = sum(1 for key in pending if not task_id or
            (effective_directory_options(self.config, self._pending_paths.get(key, key)) or {}).get('directory', {}).get('id') == task_id)
        result['discovery'] = stats
        return result

    def _dispatch_batch(self):
        if not self.registry or self._paused_all:
            return
        with self._analysis_lock:
            if self._analysis_active or self._analysis_shutdown:
                return
            batches = self.service.db.execute_query("SELECT * FROM media_watch_batches WHERE state='queued' ORDER BY created_at LIMIT 100")
            for batch in batches:
                root = next((r for r in self.config['directories'] if r['id'] == batch['task_id'] and r['enabled']), None)
                if not root or self.registry.state(root['id'])['paused']:
                    continue
                items = self.service.db.execute_query("SELECT * FROM media_watch_items WHERE batch_id=? AND stage IN ('queued','rename_ready','xmp_ready') ORDER BY id", (batch['id'],))
                if items:
                    self._analysis_active = True
                    self._active_batch_id = batch['id']
                    self._cancel_batch = threading.Event()
                    self._batch_pause_requested = threading.Event()
                    self.service.db.execute_non_query("UPDATE media_watch_batches SET state='running' WHERE id=?", (batch['id'],))
                    self._analysis_pool.submit(self._execute_batch, batch, items)
                    return
                all_items = self.service.db.execute_query('SELECT stage FROM media_watch_items WHERE batch_id=?', (batch['id'],))
                completed = sum(row['stage'] in ('done','duplicate','ignored','superseded') for row in all_items)
                total = len(all_items)
                value = {'batch_id': batch['id'], 'task_id': root['id'], 'overall_done': completed,
                         'overall_total': total, 'overall_percent': int(completed * 100 / max(1,total)),
                         'message': '全部为重复或已完成素材' if completed == total else '等待人工处理'}
                self._last_snapshot = value
                self.service.db.execute_non_query('UPDATE media_watch_batches SET state=?,snapshot=? WHERE id=?',
                    ('completed' if completed == total else 'paused', json.dumps(value), batch['id']))
                self.on_status({'snapshot': value, 'task_id': root['id']})

    def _execute_batch(self, batch, items):
        try:
            # 调度权覆盖完整批次，包括命名和 XMP；手动任务在完整批次边界接替。
            with self.service.processing_queue.claim('background', self._cancel_batch):
                self._execute_claimed_batch(batch, items)
        except InterruptedError:
            paused_now = self._paused_all or self.registry.state(batch['task_id'])['paused']
            paused = paused_now or self._batch_pause_requested.is_set()
            if not paused:
                self.service.db.execute_non_query(
                    "UPDATE media_watch_items SET stage='cancelled',updated_at=? WHERE batch_id=? "
                    "AND stage IN ('queued','rename_ready','xmp_ready')", (utc_now(), batch['id']))
            next_state = 'paused' if paused_now else 'queued' if paused else 'cancelled'
            self.service.db.execute_non_query('UPDATE media_watch_batches SET state=? WHERE id=?', (next_state, batch['id']))
            with self._analysis_lock:
                self._analysis_active = False
                self._active_batch_id = ''
            self._wake.set()

    def _execute_claimed_batch(self, batch, items):
        options = json.loads(batch['config'])
        root = options['directory']
        try:
            candidates = []
            for original_item in items:
                current = self.service.db.execute_query('SELECT * FROM media_watch_items WHERE id=?', (original_item['id'],))
                if not current or current[0]['stage'] not in ('queued','rename_ready','xmp_ready'):
                    continue
                item = current[0]
                try:
                    eligible = self.service.filter_processable_paths([item['path']])
                except OSError as exc:
                    self.registry.update_item(item['id'], item['stage'], FILE_WAIT_PREFIX + str(exc))
                    continue
                row = self.service.db.get_video_by_path(item['path']) or {}
                if row.get('content_version') != item['content_version']:
                    self.registry.update_item(item['id'], 'superseded', '文件内容版本已变化')
                elif item['path'] not in eligible:
                    self.registry.update_item(item['id'], 'duplicate' if row.get('duplicate_of') else 'review')
                else:
                    candidates.append(item)
            analyze = [item for item in candidates if item['stage'] == 'queued' and (self.service.db.get_video_by_path(item['path']) or {}).get('status') not in ('analyzed', 'renamed', 'edited')]
            def snapshot(value):
                counts = self.service.db.execute_query('SELECT stage,COUNT(*) AS count FROM media_watch_items WHERE batch_id=? GROUP BY stage', (batch['id'],))
                total = sum(row['count'] for row in counts)
                terminal = sum(row['count'] for row in counts if row['stage'] in ('done','duplicate','ignored','superseded','failed','review','cancelled','manual','pending'))
                self._last_snapshot = {**value, 'batch_id': batch['id'], 'task_id': root['id'], 'counts': counts,
                    'overall_total': total, 'overall_done': terminal, 'overall_percent': int(100*terminal/max(1,total))}
                self.service.db.execute_non_query('UPDATE media_watch_batches SET snapshot=? WHERE id=?', (json.dumps(self._last_snapshot), batch['id']))
                self.on_status({'batch_id': batch['id'], 'task_id': root['id'], 'snapshot': self._last_snapshot})
            if analyze:
                analysis_items = {path_key(item['path']): item['id'] for item in analyze}
                def item_started(path):
                    item_id = analysis_items.get(path_key(path))
                    if item_id is None:
                        return False
                    with self.service.db.lock, self.service.db._managed_connection() as connection:
                        return connection.execute(
                            "UPDATE media_watch_items SET stage='analyzing',updated_at=? "
                            "WHERE id=? AND stage IN ('queued','analyzing')",
                            (utc_now(), item_id),
                        ).rowcount > 0
                execution = {key: options[key] for key in ('max_workers', 'request_interval') if options.get(key) is not None}
                retries = copy.deepcopy(options.get('analysis_retry', self.service.settings.get('processing', {}).get('analysis_retry', {})))
                retries.update({key: options[key] for key in ('call_extra_attempts', 'item_max_attempts', 'batch_rerun_max_rounds') if options.get(key) is not None})
                execution['analysis_retry'] = retries
                execution['auto_sync_xmp_after_analysis'] = False
                self.service.run_analysis([item['path'] for item in analyze], cancel_event=self._cancel_batch,
                    callbacks={'snapshot': snapshot, 'item_started': item_started,
                        'progress': lambda done,total: None, 'log': lambda text: None}, execution_options=execution)
            for item in candidates:
                latest = self.service.db.execute_query('SELECT * FROM media_watch_items WHERE id=?', (item['id'],))[0]
                if latest['stage'] in ('ignored','manual','superseded','duplicate'):
                    continue
                item = latest
                state = self.registry.state(root['id'])
                current_root = next((r for r in self.config['directories'] if r['id'] == root['id'] and r['enabled']), None)
                if not current_root or self._paused_all or state['paused'] or self._batch_pause_requested.is_set():
                    current_video = self.service.db.get_video_by_path(latest['path']) or {}
                    if latest['stage'] == 'analyzing':
                        if current_video.get('status') in ('analyzed', 'renamed', 'edited'):
                            self.registry.update_item(item['id'], 'rename_ready')
                        else:
                            self.registry.update_item(item['id'], 'review',
                                '分析请求中断且结果未落库，请人工核对后决定是否重试。')
                    continue
                if self._cancel_batch.is_set():
                    self.registry.update_item(item['id'], 'cancelled')
                    continue
                current = self.service.db.execute_query('SELECT * FROM videos WHERE id=?', (item['video_id'],))
                row = current[0] if current else {}
                if row.get('content_version') != item['content_version']:
                    self.registry.update_item(item['id'], 'superseded')
                    continue
                if row.get('status') not in ('analyzed', 'renamed', 'edited'):
                    self.registry.update_item(item['id'], 'failed', '分析未成功，请查看失败处理中心')
                    continue
                path = row['path']
                try:
                    eligible = self.service.filter_processable_paths([path])
                except OSError as exc:
                    resume_stage = 'xmp_ready' if item['stage'] == 'xmp_ready' else 'rename_ready'
                    self.registry.update_item(item['id'], resume_stage, FILE_WAIT_PREFIX + str(exc))
                    continue
                if not eligible:
                    self.registry.update_item(item['id'], 'duplicate' if row.get('duplicate_of') else 'review')
                    continue
                if options['auto_rename'] and item['stage'] != 'xmp_ready':
                    self.registry.update_item(item['id'], 'rename_ready')
                    if self._rename_item(item, path, options, batch):
                        row = self.service.db.execute_query('SELECT * FROM videos WHERE id=?', (item['video_id'],))[0]
                        path = row['path']
                    else:
                        continue
                if self._paused_all or self.registry.state(root['id'])['paused']:
                    self.registry.update_item(item['id'], 'xmp_ready')
                    continue
                self.registry.update_item(item['id'], 'xmp_ready')
                enabled = options['xmp_mode'] == 'on'
                if enabled:
                    self.registry.update_item(item['id'], 'xmp')
                    result = self.service.sync_metadata_to_xmp([path], return_details=True)
                    if result.get('failed_count', result.get('failed', 0)):
                        self.registry.update_item(item['id'], 'xmp_ready', 'XMP写入失败，可重试该阶段')
                        continue
                self.registry.update_item(item['id'], 'done')
                self._set_state(path, '成功')
            counts = self.service.db.execute_query('SELECT stage,COUNT(*) AS count FROM media_watch_items WHERE batch_id=? GROUP BY stage', (batch['id'],))
            total = sum(row['count'] for row in counts)
            waiting = sum(row['count'] for row in counts if row['stage'] in ('queued', 'rename_ready', 'xmp_ready'))
            snapshot({'overall_done': total - waiting, 'overall_total': total, 'overall_percent': int(100 * (total - waiting) / max(1,total)), 'message': '批次处理完成' if not waiting else '批次等待继续', 'counts': counts})
            paused_now = self._paused_all or self.registry.state(root['id'])['paused'] or not any(
                directory['id'] == root['id'] and directory['enabled'] for directory in self.config['directories'])
            next_state = 'queued' if waiting and self._batch_pause_requested.is_set() and not paused_now else 'paused' if waiting else 'completed'
            self.service.db.execute_non_query('UPDATE media_watch_batches SET state=? WHERE id=?', (next_state, batch['id']))
            errors = self.service.db.execute_query("SELECT COUNT(*) AS n FROM media_watch_items WHERE batch_id=? AND (stage IN ('failed','review') OR error<>'')", (batch['id'],))[0]['n']
            self.on_status({'task_id': root['id'], 'batch_complete': True,
                'notify': options['notify_failure'] if errors else options['notify_complete'],
                'error': f'本批次有 {errors} 项需要查看，请前往素材监控处理。' if errors else '', 'counts': counts})
        except InterruptedError:
            for item in items:
                self.registry.update_item(item['id'], 'cancelled')
            self.service.db.execute_non_query("UPDATE media_watch_batches SET state='cancelled' WHERE id=?", (batch['id'],))
        except Exception as exc:
            logger.exception('监控批次失败')
            self.service.db.execute_non_query("UPDATE media_watch_items SET stage='failed',error=? WHERE batch_id=? AND stage='analyzing'", (str(exc), batch['id']))
            self.service.db.execute_non_query("UPDATE media_watch_batches SET state='failed' WHERE id=?", (batch['id'],))
            self.on_status({'task_id': root['id'], 'status': '失败', 'error': str(exc), 'notify': options['notify_failure']})
        finally:
            self.service.invalidate_video_memory_cache()
            with self._analysis_lock:
                self._analysis_active = False
                self._active_batch_id = ''
            self._wake.set()

    def _rename_item(self, item, path, options, batch):
        pattern = options['rename_pattern'] or None
        from datetime import datetime
        date_value = datetime.fromisoformat(batch['created_at']).astimezone().strftime('%Y%m%d')
        previews = self.service.batch_rename(dry_run=True, selected_paths=[path], custom_pattern=pattern, regex_find=options['regex_find'], regex_replace=options['regex_replace'], date_value=date_value)
        target = os.path.join(os.path.dirname(path), previews[0]['new']) if previews else path
        if target != path and os.path.exists(target) and options['rename_conflict'] == 'review':
            self.registry.update_item(item['id'], 'review', '目标名称冲突，等待人工处理')
            return False
        if target != path:
            self.registry.update_item(item['id'], 'renaming')
            self.service.batch_rename(selected_paths=[path], custom_pattern=pattern, regex_find=options['regex_find'], regex_replace=options['regex_replace'], session_id=batch['id'], conflict_policy=options['rename_conflict'], date_value=date_value)
            report = self.service._last_rename_result or {}
            if report.get('failed_count'):
                self.registry.update_item(item['id'], 'review', str(report.get('failed_items')))
                return False
            self.service.db.execute_non_query('UPDATE media_watch_batches SET rename_session_id=? WHERE id=?', (report.get('session_id'), batch['id']))
        return True

    def _queue_analysis(self, path: str) -> None:
        with self._analysis_lock:
            if self._analysis_shutdown:
                return
            if path == self._analysis_active_path or path in self._analysis_queue:
                return
            self._analysis_queue.append(path)
            if self._analysis_active:
                return
            self._analysis_active = True
            self._analysis_active_path = self._analysis_queue.pop(0)
            next_path = self._analysis_active_path
        self._analysis_pool.submit(self._analyze_queued_path, next_path)

    def _analyze_queued_path(self, path: str) -> None:
        try:
            self._set_state(path, "分析中")
            analysis_lock = getattr(self.service, "_analysis_run_lock", None)
            while True:
                with self._analysis_lock:
                    if self._analysis_shutdown:
                        return
                if analysis_lock is not None and analysis_lock.locked():
                    time.sleep(0.5)
                    continue
                try:
                    self.service.run_analysis(path, force_reanalyze=False)
                    break
                except Exception:
                    if analysis_lock is not None and analysis_lock.locked():
                        continue
                    raise
            row = self.service.db.get_video_by_path(path) or {}
            self._set_state(path, "成功" if row.get("status") in ("analyzed", "renamed", "edited") else "失败")
        except Exception:
            logger.exception("监控触发分析失败: %s", path)
            self._set_state(path, "失败")
        finally:
            next_path = ""
            with self._analysis_lock:
                self._analysis_active = False
                self._analysis_active_path = ""
                if self._analysis_queue and not self._analysis_shutdown:
                    self._analysis_active = True
                    self._analysis_active_path = self._analysis_queue.pop(0)
                    next_path = self._analysis_active_path
            if next_path:
                self._analysis_pool.submit(self._analyze_queued_path, next_path)
