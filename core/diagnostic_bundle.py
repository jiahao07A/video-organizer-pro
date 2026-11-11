"""仅采集白名单技术信息，离线导出脱敏诊断包与网页反馈正文。"""
from __future__ import annotations

from datetime import datetime, timezone
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import sqlite3
import sys
import threading
import time
from urllib.parse import urlencode
import uuid
import zipfile

from core.app_version import APP_VERSION, REPOSITORY
from core.diagnostic_logging import anonymous, get_runtime, register_settings, sanitize

LIMIT = 20 * 1024 * 1024
ISSUE_URL = f'https://github.com/{REPOSITORY}/issues/new'


class BundleCancelled(Exception):
    pass


class _Omissions(list):
    """损坏日志可能有百万行，缺项说明本身也必须有界。"""
    def append(self, value):
        if value not in self and len(self) < 100:
            super().append(value)


def _validated_event(value):
    if not isinstance(value, dict):
        raise ValueError('日志记录不是对象')
    if not isinstance(value.get('time'), str):
        raise ValueError('日志时间无效')
    timestamp = datetime.fromisoformat(value['time']).timestamp()
    if not isinstance(value.get('event'), str) or not isinstance(value.get('level'), str):
        raise ValueError('日志级别或事件无效')
    for key in ('session_id', 'job_id', 'item_id', 'operation_id', 'parent_operation_id', 'request_id'):
        if key in value and value[key] is not None and not isinstance(value[key], str):
            raise ValueError('日志关联编号无效')
    return sanitize(value), timestamp


def feedback_body(fields, bundle_id):
    safe = sanitize(fields)
    return (f"## 问题摘要\n{safe.get('summary', '')}\n\n## 复现步骤\n{safe.get('steps', '')}"
            f"\n\n## 期望结果\n{safe.get('expected', '')}\n\n## 实际结果\n{safe.get('actual', '')}"
            f"\n\n## 运行环境\n应用版本：{APP_VERSION}\n系统：{platform.system()} {platform.release()}"
            f"\n\n## 证据\n诊断包编号：{bundle_id}\n请将生成的 ZIP 拖入此处作为附件。\n")


def issue_url(fields, bundle_id):
    title = '[Bug] ' + str(sanitize(fields.get('summary', '问题反馈')))[:100]
    body = feedback_body(fields, bundle_id)
    safe = sanitize(fields)
    excerpt = 80
    for _ in range(12):
        url = ISSUE_URL + '?' + urlencode({'template': 'bug_report.md', 'labels': 'needs-triage', 'title': title, 'body': body})
        if len(url) <= 1800:
            return url
        excerpt //= 2
        title = title[:max(8, len(title) // 2)]
        body = ('应用版本：' + APP_VERSION + '\n诊断包编号：' + bundle_id + '\n' +
                '\n'.join(label + '：' + str(safe.get(key, ''))[:excerpt] for key, label in
                          [('steps', '复现'), ('expected', '期望'), ('actual', '实际')]) +
                '\n请从反馈窗口复制完整正文，并将ZIP拖入附件区域后提交。')
    return ISSUE_URL + '?' + urlencode({'template': 'bug_report.md', 'labels': 'needs-triage', 'title': title[:30], 'body': f'应用版本：{APP_VERSION}\n诊断包：{bundle_id}\n请复制反馈窗口中的完整正文并拖入ZIP附件。'})


def environment_snapshot(settings):
    versions = {}
    for name in ('PySide6', 'opencv-python', 'openai', 'numpy', 'packaging', 'psutil'):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = 'unavailable'
    processing = settings.get('processing', {})
    allowed = {k: v for k, v in processing.items() if k in {
        'max_workers', 'enable_audio_transcription', 'frame_interval', 'max_frames', 'scene_threshold',
        'call_extra_attempts', 'item_max_attempts', 'batch_rerun_enabled'}}
    allowed['analysis_retry'] = {k: v for k, v in processing.get('analysis_retry', {}).items() if k in {'call_extra_attempts', 'item_max_attempts', 'batch_rerun_enabled'}}
    routes = {}
    from core.model_providers import TASK_ROUTE_KEYS, resolve_task_route
    for task in TASK_ROUTE_KEYS:
        try:
            route = resolve_task_route(settings, task)
            routes[task] = {'provider': anonymous(route.get('provider_id', '')), 'model': route.get('model', '')}
        except Exception:
            routes[task] = {'available': False}
    return sanitize({'app_version': APP_VERSION, 'python': platform.python_version(),
                     'system': platform.system(), 'release': platform.release(), 'architecture': platform.machine(),
                     'packaged': bool(getattr(sys, 'frozen', False)), 'dependencies': versions,
                     'processing': allowed, 'routes': routes})


def build_bundle(destination, *, settings=None, database=None, period='session', fields=None, cancel=None, runtime=None):
    """不构造业务服务或升级数据库；各采集项失败独立记入清单。"""
    settings, fields = settings or {}, fields or {}
    register_settings(settings)
    runtime = runtime or get_runtime()
    cancel = cancel or threading.Event()
    bundle_id = uuid.uuid4().hex
    omissions, entries = _Omissions(), {}
    used = 0
    item_ids = {}
    def package_ids(value):
        if isinstance(value, dict):
            result = {}
            for key, child in value.items():
                if key == 'item_id' and isinstance(child, str):
                    result[key] = item_ids.setdefault(child, f'asset-{len(item_ids) + 1:05d}')
                else:
                    result[key] = package_ids(child)
            return result
        if isinstance(value, list):
            return [package_ids(child) for child in value]
        return value
    def check():
        if cancel.is_set():
            raise BundleCancelled('已取消诊断包生成')
    def add(name, value):
        nonlocal used
        check()
        data = (value if isinstance(value, str) else json.dumps(sanitize(package_ids(value)), ensure_ascii=False, indent=2)).encode('utf-8')
        if used + len(data) > LIMIT - 256 * 1024:
            omissions.append(name + '：超出内容容量，已略过')
            return
        entries[name] = data
        used += len(data)
    add('report/feedback.md', feedback_body(fields, bundle_id))
    add('report/说明.txt', '本包默认脱敏，不含原始素材、数据库或完整配置。匿名编号用于关联技术事件。\n将ZIP拖入GitHub反馈页面后，由你检查并提交。自动采集无法替代最小复现素材。')
    try:
        add('environment/runtime.json', environment_snapshot(settings))
    except Exception as exc:
        omissions.append('环境信息：' + str(sanitize(exc)))
    events = []
    metric_snapshots = []
    if runtime:
        try:
            runtime.flush()
        except Exception as exc:
            omissions.append('日志刷新失败，继续采集现有快照：' + str(sanitize(exc)))
        sessions = {runtime.session_id, runtime.previous_abnormal} - {''}
        cutoff = time.time() - {'24h': 86400, '7d': 604800}.get(period, 0)
        # 最多读取预算内的近期日志；逐行限制避免损坏单行造成无界内存。
        read_bytes = 0
        snapshots = []
        for path in runtime.files():
            try:
                snapshots.append((path.stat().st_mtime, path))
            except OSError:
                omissions.append('日志轮换期间有文件不可用，已略过')
        for modified, path in sorted(snapshots, reverse=True):
            check()
            if not runtime.owns_path(path):
                omissions.append('日志路径已变更或包含链接，已略过')
                continue
            if period != 'session' and modified < cutoff:
                continue
            if period == 'session' and path.name.startswith('events-') and not any(
                path.name == f'events-{session}.jsonl' or path.name.startswith(f'events-{session}.jsonl.')
                for session in sessions
            ):
                continue
            if read_bytes >= 24 * 1024 * 1024:
                if path.name.startswith('events-'):
                    omissions.append('旧日志：达到读取上限')
                    continue
            if path.name.startswith('events-'):
                try:
                    with path.open('r', encoding='utf-8', errors='replace') as source:
                        while True:
                            check()
                            line = source.readline(65537)
                            if not line:
                                break
                            read_bytes += len(line.encode('utf-8'))
                            if len(line) > 65536:
                                omissions.append('日志中有超长或损坏记录，已截断读取')
                                break
                            try:
                                item, timestamp = _validated_event(json.loads(line))
                                if period == 'session' and item.get('session_id') not in sessions:
                                    continue
                                if period != 'session' and timestamp < cutoff:
                                    continue
                                events.append(item)
                            except (ValueError, KeyError, TypeError, OverflowError, OSError, RecursionError):
                                omissions.append('日志中有无法解析的记录，已略过')
                            if read_bytes >= 24 * 1024 * 1024:
                                break
                except OSError as exc:
                    omissions.append('运行日志：' + str(sanitize(exc)))
            elif path.name.startswith('job_') and path.suffix == '.json':
                metric_snapshots.append(path)
            else:
                omissions.append('旧格式日志未纳入，避免原样复制未结构化敏感内容')
        # 错误优先，其余按时间保留最近记录；汇总按时间还原操作线索。
        for item in list(runtime.memory.copy()):
            try:
                item, timestamp = _validated_event(item)
                if period == 'session' and item.get('session_id') not in sessions:
                    continue
                if period != 'session' and timestamp < cutoff:
                    continue
                events.append(sanitize(item))
            except (ValueError, KeyError, TypeError, OverflowError, OSError, RecursionError):
                omissions.append('内存日志含无法解析的时间，已略过')
        job_ids = {e.get('job_id') for e in events if e.get('job_id')}
        unique = {json.dumps(e, ensure_ascii=False, sort_keys=True): e for e in events}
        def correlations(event):
            # 编号只在同一会话、同一种关联类型内匹配；子操作也关联父操作。
            return {(event.get('session_id'), 'operation_id' if key == 'parent_operation_id' else key, event[key])
                    for key in ('operation_id', 'parent_operation_id', 'job_id', 'request_id') if event.get(key)}
        error_links = set()
        for event in unique.values():
            if event.get('level') in ('ERROR', 'CRITICAL'):
                error_links.update(correlations(event))
        def priority(event):
            if event.get('level') in ('ERROR', 'CRITICAL'):
                return 2
            return 1 if correlations(event) & error_links else 0
        ordered = sorted(unique.values(), key=lambda e: (priority(e), e.get('time', '')), reverse=True)
        lines, size = [], 0
        for item in ordered:
            line = json.dumps(item, ensure_ascii=False)
            if size + len(line.encode('utf-8')) + 1 > min(12 * 1024 * 1024, LIMIT - used - 512 * 1024):
                omissions.append('运行日志超过包容量，已优先保留错误、关联事件及近期事件')
                continue
            lines.append(item)
            size += len(line.encode('utf-8')) + 1
        lines.sort(key=lambda e: e.get('time', ''))
        add('logs/events.jsonl', '\n'.join(json.dumps(package_ids(e), ensure_ascii=False) for e in lines))
        add('report/errors.json', [e for e in lines if e.get('level') in ('ERROR', 'CRITICAL')][-100:])
        add('report/timeline.json', [{k: e[k] for k in ('time', 'event', 'operation_id', 'operation', 'job_id', 'item_id', 'request_id') if k in e} for e in lines][-1000:])
        # 先分配错误和操作日志预算，再添加较旧的指标，避免挤掉主要诊断证据。
        metric_bytes = 0
        for path in metric_snapshots:
            check()
            if period == 'session' and path.stem.removeprefix('job_') not in job_ids:
                continue
            try:
                if not runtime.owns_path(path):
                    omissions.append('分析指标路径已变更，已略过')
                    continue
                size = path.stat().st_size
                if size > 256 * 1024 or metric_bytes + size > 4 * 1024 * 1024:
                    omissions.append('分析指标超过独立4MB读取预算或单文件256KB限制，已略过')
                    continue
                with path.open('rb') as source:
                    data = source.read(256 * 1024 + 1)
                metric_bytes += len(data)
                if len(data) > 256 * 1024:
                    omissions.append('分析指标读取时增长，已略过')
                    continue
                value = json.loads(data)
                # 自由文本不导出，指标读取不占用运行日志预算。
                value = {k: v for k, v in value.items() if isinstance(v, (bool, int, float)) or k in ('job_id', 'finished_at', 'rates')}
                add('analysis/' + path.name, value)
            except (OSError, ValueError, AttributeError, RecursionError) as exc:
                omissions.append('分析指标：' + str(sanitize(exc)))
        if runtime.warning:
            omissions.append(runtime.warning)
    else:
        omissions.append('日志服务未初始化，无运行日志')
    if database:
        try:
            with sqlite3.connect(Path(database).resolve().as_uri() + '?mode=ro', uri=True, timeout=1) as conn:
                conn.execute('PRAGMA query_only=ON')
                conn.row_factory = sqlite3.Row
                add('environment/database.json', {'user_version': conn.execute('PRAGMA user_version').fetchone()[0]})
                where, params = '', ()
                if period in ('24h', '7d'):
                    where = ' WHERE r.updated_at >= ?'
                    params = (datetime.fromtimestamp(time.time() - {'24h': 86400, '7d': 604800}[period], timezone.utc).isoformat(),)
                rows = conn.execute('SELECT f.video_id,f.category,f.stage,f.status_code,f.model,f.ever400,f.retained_result,f.attempt_count,r.updated_at,r.state FROM analysis_failure_index f JOIN video_recovery r ON r.video_id=f.video_id' + where + ' ORDER BY r.updated_at DESC LIMIT 200', params).fetchall()
                paths = {}
                if rows:
                    try:
                        paths = dict(conn.execute('SELECT id,path FROM videos WHERE id IN (' + ','.join('?' for _ in rows) + ')', tuple(row['video_id'] for row in rows)).fetchall())
                    except sqlite3.Error:
                        omissions.append('旧数据库缺少素材路径，失败历史使用独立匿名编号')
                values = []
                for row in rows:
                    item = dict(row)
                    video_id = item.pop('video_id')
                    item['item_id'] = anonymous(paths.get(video_id, video_id))
                    values.append(item)
                add('analysis/failures.json', values)
        except (OSError, sqlite3.Error) as exc:
            omissions.append('失败历史不可用：' + str(sanitize(exc)))
    omissions = list(dict.fromkeys(omissions))[:100]
    manifest = {'bundle_id': bundle_id, 'created_at': datetime.now(timezone.utc).isoformat(),
                'period': period, 'sanitized': True, 'omissions': omissions,
                'redaction': ['密钥、凭据URL、个人路径和素材名称已替换', '旧格式自由文本日志未直接复制', '配置和失败历史仅导出白名单字段'],
                'failure_selection': '近期最多200条当前失败状态；24小时/7天按更新时间过滤',
                'dropped_records': runtime.dropped if runtime else 0, 'files': list(entries)}
    add('report/manifest.json', manifest)
    destination = Path(destination)
    # 使用独占临时文件，成功后原子替换用户选定的目标；取消不留下半成品。
    temporary = destination.with_name(destination.name + '.' + bundle_id + '.part')
    try:
        with temporary.open('xb') as stream:
            with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                for name, data in entries.items():
                    check()
                    archive.writestr(name, data)
        check()
        if temporary.stat().st_size > LIMIT:
            raise ValueError('诊断包超过20MB，请缩小时间范围')
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()
    return {'path': str(destination.resolve()), 'size': destination.stat().st_size, 'bundle_id': bundle_id,
            'omissions': omissions, 'body': feedback_body(fields, bundle_id), 'url': issue_url(fields, bundle_id),
            'preview': entries['report/feedback.md'].decode('utf-8') + '\n\n' + json.dumps(manifest, ensure_ascii=False, indent=2)}
