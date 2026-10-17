"""物理整理模拟预览与冲突检查的纯逻辑。"""

from __future__ import annotations

import os
import re
from typing import Any, Callable, Dict, Iterable, List, Optional, Set


PathExists = Callable[[str], bool]
PathWritable = Callable[[str], bool]
HashMatches = Callable[[Dict[str, Any], str], Optional[bool]]

_SAFE_CATEGORY_RE = re.compile(r"^[^\\/:*?\"<>|\x00]+$")
_WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def _safe_category_name(value: Any) -> tuple[str, bool]:
    """返回单级安全分类目录名；不接受路径语法或 Windows 保留字符。"""
    category = str(value or "Other").strip()
    stem = category.rstrip(" .").upper()
    valid = bool(category) and category == category.rstrip(" .") and category not in {".", ".."}
    valid = valid and not os.path.isabs(category) and bool(_SAFE_CATEGORY_RE.fullmatch(category))
    valid = valid and os.path.basename(category) == category and stem not in _WINDOWS_RESERVED_NAMES
    return (category if valid else "Other"), valid


def _is_within_root(path: str, root: str) -> bool:
    try:
        resolved_path = os.path.realpath(os.path.abspath(path))
        resolved_root = os.path.realpath(os.path.abspath(root))
        return os.path.normcase(os.path.commonpath([resolved_path, resolved_root])) == os.path.normcase(resolved_root)
    except ValueError:
        return False


def _default_writable(path: str) -> bool:
    """检查现有父目录是否可写，不创建目录，也不改变文件。"""
    candidate = os.path.abspath(path)
    while not os.path.exists(candidate):
        parent = os.path.dirname(candidate)
        if parent == candidate:
            return False
        candidate = parent
    return os.access(candidate, os.W_OK)


def _unique_destination(
    destination: str,
    used_destinations: Set[str],
    path_exists: PathExists,
) -> tuple[str, bool]:
    """返回不覆盖的目标路径及是否发生了目标冲突。"""
    if not path_exists(destination) and destination not in used_destinations:
        return destination, False

    directory, filename = os.path.split(destination)
    base, extension = os.path.splitext(filename)
    for index in range(1, 10000):
        candidate = os.path.join(directory, f"{base}_{index}{extension}")
        if not path_exists(candidate) and candidate not in used_destinations:
            return candidate, True
    return destination, True


def build_physical_migration_plan(
    videos: Iterable[Dict[str, Any]],
    target_root: str,
    *,
    path_exists: PathExists = os.path.exists,
    path_writable: PathWritable = _default_writable,
    hash_matches: Optional[HashMatches] = None,
) -> List[Dict[str, Any]]:
    """构建只读的物理整理预览。

    该函数只计算路径和风险，不创建目录、不写文件，也不改变输入记录。
    ``hash_matches`` 返回 True/False/None；None 表示没有可比较的持久化哈希。
    """
    plan: List[Dict[str, Any]] = []
    used_destinations: Set[str] = set()
    root = os.path.abspath(str(target_root))

    for item in videos:
        source_path = str(item.get("path") or "")
        if not source_path:
            continue
        source_path = os.path.abspath(source_path)
        category, category_is_safe = _safe_category_name(item.get("category"))
        filename = os.path.basename(source_path)
        requested_target = os.path.join(root, category, filename)
        target_within_root = _is_within_root(requested_target, root)
        # 已经位于目标位置的文件不需要整理，也不能进入执行计划。
        if os.path.normcase(os.path.normpath(source_path)) == os.path.normcase(
            os.path.normpath(requested_target)
        ):
            continue
        source_exists = bool(path_exists(source_path))
        source_size = None
        source_mtime_ns = None
        if source_exists:
            try:
                source_stat = os.stat(source_path)
                source_size = source_stat.st_size
                source_mtime_ns = getattr(
                    source_stat, "st_mtime_ns", int(source_stat.st_mtime * 1e9)
                )
            except OSError:
                pass
        target_exists_before = bool(path_exists(requested_target))
        target_path, target_conflict = _unique_destination(
            requested_target, used_destinations, path_exists
        )
        used_destinations.add(target_path)

        source_xmp_path = os.path.splitext(source_path)[0] + ".xmp"
        target_xmp_path = os.path.splitext(target_path)[0] + ".xmp"
        source_xmp_exists = bool(path_exists(source_xmp_path))
        target_xmp_exists_before = bool(path_exists(target_xmp_path))

        risk_flags: List[str] = []
        if not category_is_safe:
            risk_flags.append("分类名称含路径语法，已降级为安全目录 Other")
        if not target_within_root:
            risk_flags.append("目标路径越出目标根目录")
        if not source_exists:
            risk_flags.append("源文件不存在")
        if target_conflict:
            risk_flags.append("目标已存在，已生成不覆盖路径")
        if source_xmp_exists and target_xmp_exists_before:
            risk_flags.append("目标 XMP 侧车已存在，执行时不会覆盖，源侧车将保留")
        if not path_writable(os.path.dirname(target_path)):
            risk_flags.append("目标目录不可写")

        external_change: Optional[bool] = None
        if source_exists and hash_matches is not None:
            external_change = hash_matches(item, source_path)
            if external_change is False:
                risk_flags.append("源文件可能已被外部修改")

        if not source_exists:
            status = "blocked"
        elif external_change is False:
            status = "blocked"
        elif any(flag == "目标目录不可写" for flag in risk_flags):
            status = "blocked"
        elif target_conflict or (source_xmp_exists and target_xmp_exists_before):
            status = "review"
        else:
            status = "ready"

        plan.append(
            {
                # 保留旧字段，避免执行入口和已有调用方改变行为。
                "old_path": source_path,
                "new_path": target_path,
                "filename": filename,
                "category": category,
                # 预览展示字段。
                "source_path": source_path,
                "target_path": target_path,
                "requested_target_path": requested_target,
                "source_exists": source_exists,
                "source_size": source_size,
                "source_mtime_ns": source_mtime_ns,
                "target_exists_before": target_exists_before,
                "target_conflict": target_conflict,
                "source_xmp_exists": source_xmp_exists,
                "target_xmp_exists_before": target_xmp_exists_before,
                "expected_file_hash": str(item.get("file_hash") or "").strip(),
                "external_change": external_change,
                "risk_flags": risk_flags,
                "status": status,
                "excluded": False,
            }
        )
    return plan
