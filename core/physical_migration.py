"""物理整理模拟预览与冲突检查的纯逻辑。"""

from __future__ import annotations

import os
from typing import Any, Callable, Dict, Iterable, List, Optional, Set


PathExists = Callable[[str], bool]
PathWritable = Callable[[str], bool]
HashMatches = Callable[[Dict[str, Any], str], Optional[bool]]


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
        category = str(item.get("category") or "Other")
        filename = os.path.basename(source_path)
        requested_target = os.path.join(root, category, filename)
        # 已经位于目标位置的文件不需要整理，也不能进入执行计划。
        if os.path.normcase(os.path.normpath(source_path)) == os.path.normcase(
            os.path.normpath(requested_target)
        ):
            continue
        source_exists = bool(path_exists(source_path))
        target_exists_before = bool(path_exists(requested_target))
        target_path, target_conflict = _unique_destination(
            requested_target, used_destinations, path_exists
        )
        used_destinations.add(target_path)

        risk_flags: List[str] = []
        if not source_exists:
            risk_flags.append("源文件不存在")
        if target_conflict:
            risk_flags.append("目标已存在，已生成不覆盖路径")
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
        elif target_conflict:
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
                "target_exists_before": target_exists_before,
                "target_conflict": target_conflict,
                "external_change": external_change,
                "risk_flags": risk_flags,
                "status": status,
                "excluded": False,
            }
        )
    return plan
