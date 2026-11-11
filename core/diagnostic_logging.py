"""本地诊断日志：有界队列、写盘前脱敏、会话与操作关联。"""
from __future__ import annotations

import atexit
import contextvars
import functools
import faulthandler
import hashlib
import json
import logging
import logging.handlers
import os
from pathlib import Path
import queue
import re
import sys
import threading
import time
import traceback
import uuid
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timezone

MAX_BYTES = 200 * 1024 * 1024
FILE_BYTES = 10 * 1024 * 1024
_context = contextvars.ContextVar('diagnostic_context', default={})
_runtime = None
_secrets = set()
_private_names = set()
_private_name_trie = {}
_registry_lock = threading.RLock()
_salt = None
_hooks_installed = False


def anonymous(value):
    global _salt
    with _registry_lock:
        if _salt is None:
            directory = Path(os.environ.get('LOCALAPPDATA') or Path.home()) / 'VideoOrganizerPro' / 'diagnostics'
            path = directory / 'identity.key'
            try:
                directory.mkdir(parents=True, exist_ok=True)
                try:
                    with path.open('xb') as stream:
                        stream.write(os.urandom(24))
                except FileExistsError:
                    pass
                data = path.read_bytes()
                if len(data) != 24:
                    raise ValueError('invalid identity')
                _salt = data
            except (OSError, ValueError):
                _salt = os.urandom(24)
    return hashlib.sha256(_salt + str(value).encode('utf-8', errors='replace')).hexdigest()[:16]


def register_private_path(value):
    if value:
        name = str(value).replace('\\', '/').rsplit('/', 1)[-1]
        with _registry_lock:
            if len(_private_names) < 50000 and 3 < len(name) <= 255 and name not in _private_names:
                _private_names.add(name)
                node = _private_name_trie
                for char in name:
                    node = node.setdefault(char, {})
                node[None] = name
    return anonymous(value)


def _redact_private_names(text):
    # 按文本长度查前缀树，避免每条日志逐一遍历整个素材库。
    def word(char):
        return char.isalnum() or char == '_'
    output, index = [], 0
    with _registry_lock:
        while index < len(text):
            node, end, match = _private_name_trie, index, None
            if index == 0 or not word(text[index - 1]):
                while end < len(text) and text[end] in node:
                    node = node[text[end]]
                    end += 1
                    if None in node and (end == len(text) or not word(text[end])):
                        match = (end, node[None])
            if match:
                end, name = match
                output.append('<FILE:' + anonymous(name) + '>')
                index = end
            else:
                output.append(text[index])
                index += 1
    return ''.join(output)


def register_settings(settings):
    """只记住敏感值，不记录配置内容；旧密钥保留到进程退出。"""
    def visit(value, key=''):
        if isinstance(value, dict):
            for k, v in value.items():
                visit(v, str(k))
        elif isinstance(value, list):
            for v in value:
                visit(v, key)
        elif isinstance(value, str) and value and re.search(r'key|token|password|secret', key, re.I):
            with _registry_lock:
                _secrets.add(value)
    visit(settings)


def sanitize(value):
    if isinstance(value, dict):
        return {sanitize(str(k)): ('<REDACTED>' if re.search(r'api.?key|authorization|password|secret|(?:^|_)token$|^prompt$|system_prompt|transcript|messages|content_parts', str(k), re.I) else sanitize(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        values = [sanitize(v) for v in value[:1000]]
        if len(value) > 1000:
            values.append({'truncated_items': len(value) - 1000})
        return values
    if value is None or isinstance(value, (bool, int, float)):
        return value
    text = str(value)[:65536]
    with _registry_lock:
        secrets = tuple(_secrets)
    for secret in sorted(secrets, key=len, reverse=True):
        text = text.replace(secret, '<REDACTED>')
    text = re.sub(r"https?://[^\s<>\x22\x27]+", lambda m: '<URL:' + anonymous(m[0].split('?')[0]) + '>', text, flags=re.I)
    # Authorization 的值属于整体凭据；覆盖任意认证方案及折行头。
    text = re.sub(r"(?im)\b(?:proxy-)?authorization[\t \x22\x27]*[:=][^\r\n]*(?:\r?\n[\t ]+[^\r\n]*)*", 'Authorization: <REDACTED>', text)
    # 先整体匹配认证方案与凭据，避免通用键值规则只吞掉 Bearer/Basic。
    text = re.sub(r"(?i)(?:authorization[\s\x22\x27]*[:=][\s\x22\x27]*)?(?:Bearer|Basic)\s+[^\s,;\x22\x27]+", '<REDACTED>', text)
    text = re.sub(r"(?i)(?:Bearer\s+|(?:api[_-]?key|token|password|authorization)[\s\x22\x27]*[:=][\s\x22\x27]*)[^\s,;\x22\x27]+", '<REDACTED>', text)
    text = re.sub(r'(?i)\bsk-[a-z0-9_-]+', '<REDACTED>', text)
    # 路径整体替换，保留项目代码栈由下面的 exception_frames 单独提供。
    text = re.sub(r"(?i)(?:[a-z]:[\\/]|\\\\)[^\r\n\x22\x27<>|]*", lambda m: '<PATH:' + anonymous(m[0]) + '>', text)
    text = re.sub(r"(?<![\w<])/(?:[^\s/\x22\x27<>]+/)+[^\s\x22\x27<>]*", lambda m: '<PATH:' + anonymous(m[0]) + '>', text)
    text = _redact_private_names(text)
    text = re.sub(r"(?i)[^\s<>\x22\x27/:=,;]+\.(?:mp4|mov|mkv|avi|webm|m4v|flv|wmv|wav|mp3|flac|srt|jpg|png|xmp)\b", lambda m: '<FILE:' + anonymous(m[0]) + '>', text)
    text = re.sub(r'[A-Za-z0-9+/]{120,}={0,2}', '<DATA>', text)
    return text[:16000] + ('<TRUNCATED>' if len(text) > 16000 else '')


def exception_frames(exc_info):
    if not exc_info or not exc_info[1]:
        return None
    root = Path(__file__).resolve().parent.parent
    frames = []
    for frame in traceback.extract_tb(exc_info[2]):
        try:
            name = Path(frame.filename).resolve().relative_to(root).as_posix()
        except ValueError:
            name = Path(frame.filename).name
        frames.append({'file': name, 'line': frame.lineno, 'function': frame.name})
    error = exc_info[1]
    # SDK 的异常字符串可能回显完整响应正文，只保留类型和结构化状态。
    remote = type(error).__module__.split('.')[0] in {'openai', 'httpx', 'httpcore', 'requests'}
    return {'type': type(error).__name__, 'message': '<远程接口异常正文已省略>' if remote else sanitize(str(error)), 'frames': frames}


def event(name, level=logging.DEBUG, **fields):
    logging.getLogger('VideoOrganizer.diagnostics').log(level, name, extra={'diagnostic_fields': fields})


@contextmanager
def operation(name, **fields):
    parent = _context.get()
    token = _context.set({**parent, 'parent_operation_id': parent.get('operation_id'), 'operation_id': uuid.uuid4().hex, 'operation': name, **fields})
    started = time.monotonic()
    event('operation.start')
    try:
        yield
    except BaseException:
        logging.getLogger('VideoOrganizer.diagnostics').exception('operation.failed')
        raise
    else:
        event('operation.end', elapsed_ms=round((time.monotonic() - started) * 1000, 2))
    finally:
        _context.reset(token)


def traced(name=None):
    def decorate(fn):
        @functools.wraps(fn)
        def call(*args, **kwargs):
            if args and hasattr(args[0], 'settings'):
                register_settings(args[0].settings)
            with operation(name or fn.__qualname__):
                result = fn(*args, **kwargs)
                if isinstance(result, dict):
                    event('operation.result', **{k: v for k, v in result.items() if k in ('attempted', 'succeeded', 'failed', 'cancelled', 'success_count', 'failed_count', 'skipped') and isinstance(v, (bool, int, float))})
                elif isinstance(result, bool):
                    event('operation.result', ok=result)
                return result
        return call
    return decorate


def trace_item(fn):
    @functools.wraps(fn)
    def call(self, path, *args, **kwargs):
        values = {'item_id': register_private_path(path)}
        metrics = getattr(self, '_job_metrics', None)
        if metrics:
            values['job_id'] = metrics.job_id
        return in_context(values, fn, self, path, *args, **kwargs)
    return call


def in_context(values, fn, *args, **kwargs):
    token = _context.set({**_context.get(), **values})
    try:
        return fn(*args, **kwargs)
    finally:
        _context.reset(token)


def contextual(fn):
    ctx = contextvars.copy_context()
    return functools.wraps(fn)(lambda *args, **kwargs: ctx.run(fn, *args, **kwargs))


def bind_worker(worker):
    worker._diagnostic_context = contextvars.copy_context()


def worker_run(fn):
    @functools.wraps(fn)
    def call(self, *args, **kwargs):
        ctx = getattr(self, '_diagnostic_context', None) or contextvars.copy_context()
        def run():
            with operation(type(self).__name__):
                return fn(self, *args, **kwargs)
        return ctx.run(run)
    return call


def worker_exception():
    logging.getLogger('VideoOrganizer.worker').exception('worker.failed')


class _Sink(logging.Handler):
    def __init__(self, runtime):
        super().__init__(logging.DEBUG)
        self.runtime = runtime

    def emit(self, record):
        if self.runtime.closed:
            return
        try:
            payload = {'time': datetime.now(timezone.utc).isoformat(), 'level': record.levelname,
                       'module': record.name, 'process': record.process, 'thread': record.threadName,
                       'session_id': self.runtime.session_id, **_context.get(),
                       'event': record.getMessage(), **getattr(record, 'diagnostic_fields', {})}
            active_exception = record.exc_info or sys.exc_info()
            if active_exception[1]:
                payload['exception'] = exception_frames(active_exception)
                error = active_exception[1]
                if type(error).__module__.split('.')[0] in {'openai', 'httpx', 'httpcore', 'requests'}:
                    payload['event'] = payload['event'].replace(str(error), '<远程接口异常正文已省略>')
            payload = sanitize(payload)
            encoded = json.dumps(payload, ensure_ascii=False)
            if len(encoded.encode('utf-8')) > 65536:
                payload = {k: payload[k] for k in ('time', 'level', 'module', 'session_id', 'operation_id', 'job_id', 'item_id') if k in payload}
                payload.update(event='logging.record_truncated', truncated=True)
            self.runtime.memory.append(payload)
            try:
                self.runtime.queue.put_nowait(payload)
            except queue.Full:
                self.runtime.dropped += 1
                if record.levelno >= logging.ERROR:
                    self.runtime.write(payload, timeout=0.05)
        except Exception:
            self.runtime.dropped += 1


class DiagnosticRuntime:
    def __init__(self, directory, *, queue_size=4096, budget=MAX_BYTES):
        self.session_id = uuid.uuid4().hex
        self.directory = Path(directory).resolve()
        self.directories = {self.directory}
        self.memory = deque(maxlen=1000)
        self.queue = queue.Queue(maxsize=queue_size)
        self.dropped = 0
        self.warning = ''
        self.budget = budget
        self.lock = threading.RLock()
        self.closed = False
        self.handler = None
        self.last_prune = 0.0
        self.retained_size = 0
        self.previous_abnormal = ''
        self.previous_abnormal_at = 0.0
        self.started_at = time.time()
        self.crashed = False
        self.native_pipe = None
        self._open()
        self.sink = _Sink(self)
        self.thread = threading.Thread(target=self._consume, name='diagnostic-writer', daemon=True)
        self.thread.start()

    def _open(self):
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            marker = self.directory / 'session-state.json'
            try:
                previous = json.loads(marker.read_text(encoding='utf-8'))
                candidate = previous.get('previous_abnormal', '')
                candidate_at = float(previous.get('previous_abnormal_at') or marker.stat().st_mtime)
                if not previous.get('clean_exit') and previous.get('session_id') != self.session_id:
                    candidate = previous.get('session_id', '')
                    candidate_at = float(previous.get('started_at') or marker.stat().st_mtime)
                if isinstance(candidate, str) and candidate and candidate_at >= self.previous_abnormal_at:
                    self.previous_abnormal, self.previous_abnormal_at = candidate, candidate_at
            except (OSError, ValueError, TypeError, AttributeError, RecursionError):
                pass
            marker.write_text(json.dumps(self._session_state(False)), encoding='utf-8')
            self.handler = logging.handlers.RotatingFileHandler(self.directory / f'events-{self.session_id}.jsonl', maxBytes=min(FILE_BYTES, max(1024, self.budget // 4)), backupCount=19, encoding='utf-8', delay=True)
            self.handler.setFormatter(logging.Formatter('%(message)s'))
            self.handler.handleError = lambda record: self._disk_failed()
            self.warning = ''
            self.prune()
        except (OSError, ValueError, TypeError, AttributeError):
            self._disk_failed()

    def _session_state(self, clean):
        return {'session_id': self.session_id, 'started_at': self.started_at, 'clean_exit': clean,
                'previous_abnormal': self.previous_abnormal, 'previous_abnormal_at': self.previous_abnormal_at}

    def _close_handler(self):
        if self.handler:
            try:
                self.handler.close()
            except (OSError, ValueError):
                self._disk_failed()

    def enable_native_crashes(self):
        """faulthandler 写入管道，由后台线程脱敏后落盘；不生成原始路径文件。"""
        if self.native_pipe is not None:
            return
        try:
            read_fd, write_fd = os.pipe()
            writer = os.fdopen(write_fd, 'wb', buffering=0)
            faulthandler.enable(file=writer, all_threads=True)
            self.native_pipe = writer
            def drain():
                with os.fdopen(read_fd, 'r', encoding='utf-8', errors='replace') as source:
                    for line in source:
                        event('native.stack', logging.CRITICAL, stack_line=line.rstrip())
            threading.Thread(target=drain, name='native-crash-reader', daemon=True).start()
        except (OSError, RuntimeError, ValueError):
            event('native.capture_unavailable', logging.WARNING)

    def _disk_failed(self):
        self.warning = '诊断日志无法写入磁盘，暂存最近1000条；请检查目录权限和可用空间。'

    def files(self):
        found = []
        for root in self.directories:
            for pattern in ('events-*.jsonl*', 'crash-*.log', 'analysis_jobs/job_*.json', 'analysis_jobs/jobs_index.jsonl*', 'analysis_jobs/analysis.log*'):
                found.extend(p for p in root.glob(pattern) if self.owns_path(p) and p.is_file())
        return found

    def owns_path(self, path):
        """拒绝文件或中间目录的符号链接/Windows联接，清理前再次检查。"""
        path = Path(path).absolute()
        for root in self.directories:
            try:
                relative = path.relative_to(root)
                current = root
                for part in relative.parts:
                    current = current / part
                    if current.is_symlink() or getattr(os.path, 'isjunction', lambda _: False)(current):
                        return False
                path.resolve().relative_to(root)
                return True
            except (OSError, ValueError):
                continue
        return False

    def prune(self, reserve=0):
        snapshots = []
        for path in self.files():
            try:
                stat = path.stat()
                snapshots.append((stat.st_mtime, path, stat.st_size))
            except OSError:
                continue
        snapshots.sort()
        total = sum(size for _, _, size in snapshots)
        active = Path(self.handler.baseFilename) if self.handler else None
        for _, path, size in snapshots:
            if total + reserve <= self.budget:
                break
            if path == active:
                continue
            if not self.owns_path(path):
                continue
            try:
                path.unlink()  # 只删除已知日志目录内一个明确的旧文件。
                total -= size
            except OSError:
                self._disk_failed()
        self.retained_size = total

    def write(self, payload, timeout=None):
        acquired = self.lock.acquire() if timeout is None else self.lock.acquire(timeout=timeout)
        if not acquired:
            self.warning = '日志写入繁忙，严重错误已保留在内存快照。'
            return
        try:
            if self.handler:
                try:
                    text = json.dumps(payload, ensure_ascii=False)
                    size = len(text.encode('utf-8')) + 2
                    if time.monotonic() - self.last_prune > 1 or self.retained_size + size > self.budget:
                        self.prune(reserve=size)
                        self.last_prune = time.monotonic()
                    if self.retained_size + size > self.budget:
                        self.warning = '日志容量已满，保留内存日志，等待旧文件可清理。'
                        self.dropped += 1
                        return
                    self.handler.emit(logging.makeLogRecord({'msg': text, 'levelno': logging.INFO}))
                    self.retained_size += size
                except (OSError, ValueError):
                    self._disk_failed()
        finally:
            self.lock.release()

    def _consume(self):
        while True:
            value = self.queue.get()
            try:
                if value is None:
                    return
                if isinstance(value, threading.Event):
                    value.set()
                    continue
                try:
                    self.write(value)
                except Exception:
                    self._disk_failed()
                    self.dropped += 1
            finally:
                self.queue.task_done()

    def flush(self):
        if not self.closed and self.thread.is_alive():
            barrier = threading.Event()
            try:
                self.queue.put(barrier, timeout=2)
                if not barrier.wait(3):
                    self.warning = '日志刷新超时，诊断包可能缺少最后几条事件。'
                    return
            except queue.Full:
                self.warning = '日志队列繁忙，诊断包可能缺少最后几条事件。'
                return
        if not self.lock.acquire(timeout=1):
            self.warning = '日志刷新超时，诊断包可能缺少最后几条事件。'
            return
        try:
            if self.handler:
                try:
                    self.handler.flush()
                except (OSError, ValueError):
                    self._disk_failed()
        finally:
            self.lock.release()

    def relocate(self, directory):
        target = Path(directory).resolve()
        if target == self.directory:
            return
        self.flush()
        if not self.lock.acquire(timeout=1):
            self.warning = '日志目录切换超时，继续保留现有日志和内存快照。'
            return
        try:
            self._close_handler()
            self.handler = None
            self.directory = target
            self.directories.add(target)
            self._open()
        finally:
            self.lock.release()
        event('logging.directory_ready', previous_abnormal=self.previous_abnormal)

    def close(self):
        if self.closed:
            return
        event('session.exit', dropped_records=self.dropped)
        self.flush()
        self.closed = True
        logging.getLogger('VideoOrganizer').removeHandler(self.sink)
        try:
            self.queue.put(None, timeout=3)
        except queue.Full:
            self._disk_failed()
            return
        self.thread.join(timeout=3)
        if self.native_pipe is not None:
            faulthandler.disable()
            self.native_pipe.close()
            self.native_pipe = None
        if not self.lock.acquire(timeout=1):
            self.warning = '日志关闭超时，保留异常退出标记。'
            return
        try:
            self._close_handler()
            for directory in self.directories:
                try:
                    marker = directory / 'session-state.json'
                    existing = json.loads(marker.read_text(encoding='utf-8'))
                    if existing.get('session_id') == self.session_id:
                        marker.write_text(json.dumps(self._session_state(not self.crashed)), encoding='utf-8')
                except (OSError, ValueError, AttributeError, RecursionError):
                    pass
        finally:
            self.lock.release()


def get_runtime():
    return _runtime


def initialize(directory=None):
    global _runtime
    if _runtime and not _runtime.closed:
        if directory:
            _runtime.relocate(directory)
        return _runtime
    directory = directory or Path(os.environ.get('LOCALAPPDATA') or Path.home()) / 'VideoOrganizerPro' / 'diagnostics'
    _runtime = DiagnosticRuntime(directory)
    root = logging.getLogger('VideoOrganizer')
    root.setLevel(logging.DEBUG)
    for h in list(root.handlers):
        if isinstance(h, _Sink):
            root.removeHandler(h)
    root.addHandler(_runtime.sink)
    # 历史分析文件 handler 不能继续绕过脱敏输出。
    for h in list(root.handlers):
        if getattr(h, '_analysis_job_file', False):
            root.removeHandler(h)
            h.close()
    # 控制台不再接收未脱敏的应用日志。
    root.propagate = False
    event('session.start', previous_abnormal=_runtime.previous_abnormal)
    atexit.register(_runtime.close)
    return _runtime


def install_exception_hooks():
    global _hooks_installed
    if _hooks_installed:
        return
    _hooks_installed = True
    old_sys, old_thread = sys.excepthook, threading.excepthook
    def unhandled(kind, value, tb):
        if _runtime:
            _runtime.crashed = True
        logging.getLogger('VideoOrganizer.crash').critical('unhandled.exception', exc_info=(kind, value, tb))
        if _runtime:
            _runtime.flush()
        old_sys(kind, value, tb)
    def thread_error(args):
        if _runtime:
            _runtime.crashed = True
        logging.getLogger('VideoOrganizer.crash').critical('thread.exception', exc_info=(args.exc_type, args.exc_value, args.exc_traceback))
        old_thread(args)
    sys.excepthook, threading.excepthook = unhandled, thread_error


def install_qt_logging():
    from PySide6.QtCore import QtMsgType, qInstallMessageHandler
    def message(kind, context, text):
        level = {QtMsgType.QtDebugMsg: logging.DEBUG, QtMsgType.QtInfoMsg: logging.INFO,
                 QtMsgType.QtWarningMsg: logging.WARNING, QtMsgType.QtCriticalMsg: logging.ERROR,
                 QtMsgType.QtFatalMsg: logging.CRITICAL}.get(kind, logging.INFO)
        event('qt.message', level, message=text, category=context.category)
    qInstallMessageHandler(message)
