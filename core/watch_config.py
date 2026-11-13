"""监控配置的唯一默认值和继承规则。"""
import fnmatch
import os
import uuid

from core.media_registry import path_key

DEFAULTS = {
    'recursive': True, 'auto_analyze': True, 'auto_rename': False,
    'start_with_windows': True, 'initial_scan_mode': 'all', 'ask_initial_scan': False,
    'stability_seconds': 30.0, 'check_interval': 5.0, 'rescan_interval': 300.0,
    'batch_size': 100, 'max_workers': None, 'request_interval': None,
    'call_extra_attempts': None, 'item_max_attempts': None, 'batch_rerun_max_rounds': None,
    'extensions': ['.mp4', '.mov', '.avi', '.mkv', '.m4v', '.flv', '.wmv'],
    'include_patterns': [], 'exclude_patterns': [], 'excluded_dirs': [],
    'rename_preset': '', 'rename_pattern': '', 'regex_find': '', 'regex_replace': '',
    'rename_conflict': 'review', 'xmp_mode': 'inherit',
    'notify_complete': False, 'notify_failure': True,
}
LIMITS = {
    'stability_seconds': (0, 3600), 'check_interval': (.1, 300),
    'rescan_interval': (1, 86400), 'batch_size': (1, 1000),
    'max_workers': (1, 512), 'request_interval': (0, 3600),
    'call_extra_attempts': (0, 10), 'item_max_attempts': (1, 10),
    'batch_rerun_max_rounds': (0, 1),
}


def _value(key, value, default):
    if key in LIMITS:
        if value is None:
            return default
        try:
            minimum, maximum = LIMITS[key]
            result = max(minimum, min(maximum, float(value)))
            return int(result) if key in ('batch_size', 'max_workers', 'call_extra_attempts', 'item_max_attempts', 'batch_rerun_max_rounds') else result
        except (ValueError, TypeError):
            return default
    if isinstance(default, bool):
        return value if isinstance(value, bool) else default
    if isinstance(default, list):
        if isinstance(value, str):
            value = value.replace('\n', ';').split(';')
        return [str(item).strip() for item in value if str(item).strip()] if isinstance(value, list) else list(default)
    if key == 'initial_scan_mode':
        return value if value in ('all', 'new') else default
    if key == 'rename_conflict':
        return value if value in ('review', 'suffix') else default
    if key == 'xmp_mode':
        return value if value in ('inherit', 'on', 'off') else default
    return str(value) if value is not None else default


def normalize_watch_config(config):
    config = config if isinstance(config, dict) else {}
    result = {key: _value(key, config.get(key, default), default) for key, default in DEFAULTS.items()}
    result['directories'] = []
    seen = set()
    for raw in config.get('directories') or []:
        raw = {'path': raw} if isinstance(raw, str) else raw
        if not isinstance(raw, dict) or not raw.get('path'):
            continue
        path = os.path.abspath(os.path.expanduser(str(raw['path'])))
        identity = path_key(path).rstrip('/')
        if identity in seen:
            continue
        seen.add(identity)
        directory = {
            'id': str(raw.get('id') or uuid.uuid5(uuid.NAMESPACE_URL, identity)),
            'name': str(raw.get('name') or os.path.basename(path) or path),
            'path': path, 'enabled': raw.get('enabled', True) is not False,
        }
        for key, default in DEFAULTS.items():
            directory[key] = _value(key, raw[key], default) if raw.get(key) is not None else None
        result['directories'].append(directory)
    return result


def effective_directory_options(config, path):
    key = path_key(path).rstrip('/')
    candidates = [(len(path_key(d['path'])), d) for d in config['directories']
                  if key == path_key(d['path']).rstrip('/') or key.startswith(path_key(d['path']).rstrip('/') + '/')]
    if not candidates:
        return None
    directory = max(candidates, key=lambda pair: pair[0])[1]
    if not directory['enabled']:
        return None
    result = {name: config[name] if directory.get(name) is None else directory[name] for name in DEFAULTS}
    result['directory'] = directory
    return result


def is_path_watchable(config, path):
    options = effective_directory_options(config, path)
    if not options:
        return False
    root = options['directory']['path']
    relative = os.path.relpath(path, root).replace('\\', '/')
    if not options['recursive'] and os.path.dirname(os.path.abspath(path)) != root:
        return False
    if os.path.splitext(path)[1].lower() not in {x.lower() if x.startswith('.') else '.' + x.lower() for x in options['extensions']}:
        return False
    if any(path_key(path).startswith(path_key(os.path.join(root, item)).rstrip('/') + '/') for item in options['excluded_dirs']):
        return False
    match = lambda pattern: fnmatch.fnmatch(relative.lower(), pattern.lower()) or fnmatch.fnmatch(os.path.basename(path).lower(), pattern.lower())
    return (not options['include_patterns'] or any(map(match, options['include_patterns']))) and not any(map(match, options['exclude_patterns']))
