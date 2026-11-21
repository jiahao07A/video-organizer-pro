"""配置文件的无损适配、版本门禁及升级前原始快照。"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Callable

from packaging.version import InvalidVersion, Version

from core.storage_backup import STORAGE_IO_LOCK, write_json_atomic

SCHEMA_KEY = "config_schema_version"
SCHEMA_VERSION = 1
TAG_VERSION = "6.0"

DEFAULT_TAG_CONFIG = {
    "version": "6.0",
    "global_settings": {
        "system_prompt": "你是一个资深的影视后期素材整理专家。请通过观察视频帧，提取精准的元数据。",
        "language": "zh-CN",
        "output_format": "json",
        "export_schemes": {
            "filename_pattern": "{category}-{tags}-{summary}",
            "xmp_hierarchical": True,
            "xmp_prefix_category": True,
            "ale_columns": ["Name", "Keywords", "Category", "Summary"]
        }
    },
    "categories": [
        { "id": "aroll", "display_name": "A-Roll (访谈/主体)", "order": 0 },
        { "id": "broll", "display_name": "B-Roll (空镜/素材)", "order": 1 },
        { "id": "working", "display_name": "工作记录", "order": 2 }
    ],
    "tag_groups": []
}


class ConfigurationError(ValueError):
    """配置不可安全读取或升级；错误消息不带配置值。"""


def check_schema(config: dict) -> None:
    if not isinstance(config, dict):
        raise ConfigurationError("配置文件根节点必须是 JSON 对象；原文件未修改。")
    version = config.get(SCHEMA_KEY, 0)
    if type(version) is not int or version < 0:
        raise ConfigurationError("配置文件格式版本无效；原文件未修改。")
    if version > SCHEMA_VERSION:
        raise ConfigurationError("配置文件来自更新的软件版本，请升级软件后重试；禁止降级覆盖。")


def _invalid(path: str):
    raise ConfigurationError(f"配置文件字段 {path} 的类型或内容无效；请从备份恢复或检查该字段。") from None


def _merge(raw: dict, defaults: dict, path: str = "") -> dict:
    """只补缺失值，兼容确定的标量类型；不把损坏结构当默认值覆盖。"""
    out = copy.deepcopy(raw)
    for key, default in defaults.items():
        field = f"{path}.{key}" if path else key
        if key not in out:
            out[key] = copy.deepcopy(default)
            continue
        value = out[key]
        if isinstance(default, dict):
            if not isinstance(value, dict):
                _invalid(field)
            out[key] = _merge(value, default, field)
        elif isinstance(default, bool):
            if isinstance(value, str) and value.lower().strip() in ("true", "false"):
                out[key] = value.lower().strip() == "true"
            elif type(value) is not bool:
                _invalid(field)
        elif isinstance(default, (int, float)):
            try:
                if isinstance(value, bool) or not isinstance(value, (str, int, float)):
                    _invalid(field)
                number = float(value)
                if not math.isfinite(number) or (isinstance(default, int) and not number.is_integer()):
                    _invalid(field)
                out[key] = int(number) if isinstance(default, int) else number
            except (ValueError, OverflowError):
                _invalid(field)
        elif isinstance(default, list):
            if not isinstance(value, list):
                _invalid(field)
        elif isinstance(default, str) and not isinstance(value, str):
            _invalid(field)
    return out


def adapt_settings(raw: dict, defaults: dict) -> dict:
    check_schema(raw)
    source = copy.deepcopy(raw)
    from core.watch_config import DEFAULTS, LIMITS, normalize_watch_config
    processing = source.get("processing")
    watch = processing.get("media_watch") if isinstance(processing, dict) else None
    if isinstance(watch, dict):
        directories = watch.get("directories", [])
        if not isinstance(directories, list):
            _invalid("processing.media_watch.directories")
        for entry in [watch, *[d for d in directories if isinstance(d, dict)]]:
            for key, default in DEFAULTS.items():
                if isinstance(default, list) and isinstance(entry.get(key), str):
                    entry[key] = [s.strip() for s in entry[key].replace("\n", ";").split(";") if s.strip()]
    out = _merge(source, defaults)
    providers = out.get("model_providers", [])
    ids = set()
    for provider in providers:
        if not isinstance(provider, dict):
            _invalid("model_providers")
        _merge(provider, {"id": "", "display_name": "", "api_key": "", "base_url": "", "models": {}}, "model_providers")
        pid = provider.get("id", "").strip()
        if pid and pid in ids:
            _invalid("model_providers.id")
        ids.add(pid)
    from core.model_providers import MODEL_KEYS, TASK_ROUTE_KEYS, ensure_providers
    for provider in providers:
        for key in MODEL_KEYS:
            if key in provider.get("models", {}) and not isinstance(provider["models"][key], str):
                _invalid("model_providers.models")
    routes = out.get("task_model_routing", {})
    for key in TASK_ROUTE_KEYS:
        if key in routes:
            if not isinstance(routes[key], dict):
                _invalid("task_model_routing")
            _merge(routes[key], {"provider_id": "", "model": ""}, "task_model_routing")
    watch = out["processing"]["media_watch"]
    seen_paths = set()
    for directory in watch["directories"]:
        if isinstance(directory, str) and directory.strip():
            continue
        if not isinstance(directory, dict) or not isinstance(directory.get("path"), str) or not directory["path"].strip():
            _invalid("processing.media_watch.directories.path")
        if "enabled" in directory:
            directory["enabled"] = _merge({"enabled": directory["enabled"]}, {"enabled": True})["enabled"]
        for key, default in DEFAULTS.items():
            if directory.get(key) is not None and default is not None:
                directory[key] = _merge({key: directory[key]}, {key: default}, "processing.media_watch.directories")[key]
    from core.media_registry import path_key
    for directory in watch["directories"]:
        path = directory if isinstance(directory, str) else directory["path"]
        identity = path_key(os.path.abspath(os.path.expanduser(path))).rstrip("/")
        if identity in seen_paths:
            _invalid("processing.media_watch.directories（重复目录）")
        seen_paths.add(identity)
    # 可继承的数值默认值为 None，仍须验证非空输入，避免无效覆盖静默消失。
    for entry in [watch, *[d for d in watch["directories"] if isinstance(d, dict)]]:
        for key in LIMITS:
            if entry.get(key) is not None:
                default = 1 if key in ("batch_size", "max_workers", "call_extra_attempts", "item_max_attempts", "batch_rerun_max_rounds") else 1.0
                entry[key] = _merge({key: entry[key]}, {key: default}, "processing.media_watch")[key]
    out["processing"]["media_watch"] = normalize_watch_config(watch)
    ensure_providers(out)
    out[SCHEMA_KEY] = SCHEMA_VERSION
    return out


def _check_tag_version(raw: dict) -> Version:
    try:
        version = Version(str(raw.get("version", "1.0")))
    except InvalidVersion:
        raise ConfigurationError("标签配置文件业务版本无效；原文件未修改。") from None
    if version > Version(TAG_VERSION):
        raise ConfigurationError("标签配置文件来自更新的软件版本，请升级软件后重试；禁止降级覆盖。")
    return version


def guard_tag_projection(path: str) -> None:
    """DB 导入恢复允许修复损坏投影，但不得覆盖未来版本的有效文件。"""
    file = Path(path)
    if not file.exists():
        return
    try:
        raw = json.loads(file.read_bytes().decode("utf-8-sig"))
    except (UnicodeError, ValueError):
        return
    if isinstance(raw, dict):
        check_schema(raw)
        _check_tag_version(raw)


def adapt_tag_config(raw: dict, defaults: dict | None = None) -> dict:
    check_schema(raw)
    version = _check_tag_version(raw)
    source = copy.deepcopy(raw)
    if "config" in source and not isinstance(source["config"], list):
        _invalid("config")
    if version < Version(TAG_VERSION) and "tag_groups" not in source:
        groups = []
        for item in source.get("config", []):
            if not isinstance(item, dict):
                _invalid("config")
            item = _merge(item, {"exclusive": True, "ai_expandable": False, "max_count": 1, "description": "", "tags": [], "rules": {}}, "config")
            group = copy.deepcopy(item)
            group.update({"id": item.get("id"), "name": item.get("display_name", item.get("id")),
                          "rules": {**item.get("rules", {}), "selection_mode": "single" if item["exclusive"] else "multiple",
                                    "max_count": item["max_count"], "ai_expandable": item["ai_expandable"], "local_prompt": item["description"]},
                          "tags": item["tags"]})
            groups.append(group)
        source["tag_groups"] = groups
    out = _merge(source, DEFAULT_TAG_CONFIG if defaults is None else defaults)
    seen = set()
    for group in out["tag_groups"]:
        if not isinstance(group, dict) or not isinstance(group.get("id"), str) or not group["id"] or group["id"] in seen:
            _invalid("tag_groups.id")
        seen.add(group["id"])
        _merge(group, {"rules": {}, "tags": []}, "tag_groups")
        if "rules" in group:
            group["rules"] = _merge(group["rules"], {}, "tag_groups.rules")
            for key, default in {"ai_expandable": False, "max_count": 1, "local_prompt": "", "selection_mode": "single"}.items():
                if key in group["rules"]:
                    group["rules"][key] = _merge({key: group["rules"][key]}, {key: default}, "tag_groups.rules")[key]
    from core.tag_vocab import ensure_canonical_tag_groups
    out = ensure_canonical_tag_groups(out)
    out["version"] = TAG_VERSION
    out[SCHEMA_KEY] = SCHEMA_VERSION
    return out


def _read(path: Path) -> tuple[bytes, dict]:
    try:
        content = path.read_bytes()
        payload = json.loads(content.decode("utf-8-sig"))
        check_schema(payload)
        return content, payload
    except ConfigurationError:
        raise
    except (OSError, UnicodeError, ValueError):
        raise ConfigurationError("配置文件读取失败，原文件未修改；请检查权限、JSON 格式或从备份恢复。") from None


def _backup_source(path: Path, content: bytes) -> Path:
    """以源内容摘要命名，重试复用同一原始快照；包含密钥，按本地配置保护。"""
    folder = path.parent / "backups" / "config-upgrades"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{path.name}.{hashlib.sha256(content).hexdigest()}.bak"
    if target.exists():
        if target.read_bytes() != content:
            raise ConfigurationError("配置升级备份校验失败，原配置未修改。")
        return target
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=folder, prefix=".upgrade-", suffix=".tmp", delete=False) as handle:
            temporary = handle.name
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)
    return target


def prepare_config_write(path: str, adapter: Callable[[dict], dict]) -> None:
    """调用方在 STORAGE_IO_LOCK 内写入前检查磁盘版本，旧结构先留快照。"""
    file = Path(path)
    if file.exists():
        content, raw = _read(file)
        if adapter(raw) != raw:
            _backup_source(file, content)
        if file.read_bytes() != content:
            raise ConfigurationError("配置文件已被其他程序修改，请重新加载后重试。")


def prepare_tag_projection_write(path: str, config: dict) -> None:
    """仅供已有 DB 导入恢复记录使用；修复前连损坏源也逐字节保留。"""
    file = Path(path)
    if not file.exists():
        return
    content = file.read_bytes()
    guard_tag_projection(path)
    try:
        raw = json.loads(content.decode("utf-8-sig"))
    except (UnicodeError, ValueError):
        raw = None
    if raw != config:
        _backup_source(file, content)
    if file.read_bytes() != content:
        raise ConfigurationError("配置文件已被其他程序修改，请重新加载后重试。")


def load_config(path: str, adapter: Callable[[dict], dict]) -> dict:
    """不存在只返回内存默认值；已有文件适配成功后先备份再原子升级。"""
    with STORAGE_IO_LOCK:
        file = Path(path)
        if not file.exists():
            return adapter({})
        content, raw = _read(file)
        result = adapter(raw)
        if result != raw:
            try:
                _backup_source(file, content)
                if file.read_bytes() != content:
                    raise ConfigurationError("配置文件已被其他程序修改，请重新加载后重试。")
                write_json_atomic(str(file), result)
            except ConfigurationError:
                raise
            except OSError:
                raise ConfigurationError("配置文件升级失败，原文件及已有备份已保留；请检查磁盘空间和写入权限后重试。") from None
        return result
