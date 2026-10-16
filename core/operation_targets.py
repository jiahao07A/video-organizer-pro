# -*- coding: utf-8 -*-
"""操作目标集：从列表选中 / 可见列表解析操作目标（纯逻辑，可单测）。

规则（与 CONTEXT 一致）：
- 有列表选中路径（非空）→ 用选中集合（保持输入顺序、去重）
- 无选中（None 或空）→ 用当前可见列表全部
- 可选工作范围约束：结果必须 ⊆ scope；范围外选中被裁掉
- 不依赖 checked_items / 勾选列概念
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from core.analysis_targets import path_status_key


@dataclass(frozen=True)
class OperationTargetSnapshot:
    """一次批量操作使用的目标快照，供摘要、确认和执行共用。"""

    selected_paths: Tuple[str, ...]
    visible_paths: Tuple[str, ...]
    target_paths: Tuple[str, ...]
    scope_paths: Optional[Tuple[str, ...]]

    @property
    def source(self) -> str:
        return "selected" if self.selected_paths else "visible"

    @property
    def selected_count(self) -> int:
        return len(self.selected_paths)

    @property
    def visible_count(self) -> int:
        return len(self.visible_paths)

    @property
    def target_count(self) -> int:
        return len(self.target_paths)


def _unique_paths(paths: Optional[Sequence[str]]) -> Tuple[str, ...]:
    """按规范化路径键去重，同时保留第一次出现的原始路径。"""
    out: List[str] = []
    seen = set()
    for path in paths or []:
        if not path:
            continue
        key = path_status_key(path)
        if not key or key in seen:
            continue
        out.append(path)
        seen.add(key)
    return tuple(out)


def resolve_operation_target_snapshot(
    selected_paths: Optional[Sequence[str]],
    visible_paths: Sequence[str],
    *,
    scope_paths: Optional[Sequence[str]] = None,
) -> OperationTargetSnapshot:
    """解析一次批量操作的选中、可见和实际目标路径快照。"""
    selected = _unique_paths(selected_paths)
    visible = _unique_paths(visible_paths)
    scope = None if scope_paths is None else _unique_paths(scope_paths)
    target = resolve_operation_target_paths(
        selected,
        visible,
        scope_paths=scope,
    )
    return OperationTargetSnapshot(
        selected_paths=selected,
        visible_paths=visible,
        target_paths=tuple(target),
        scope_paths=scope,
    )


def resolve_operation_target_paths(
    selected_paths: Optional[Sequence[str]],
    visible_paths: Sequence[str],
    *,
    scope_paths: Optional[Sequence[str]] = None,
) -> List[str]:
    """
    解析操作目标集路径列表。

    - selected 非空 → base = selected；否则 base = visible
    - 若 scope_paths 非 None：过滤到 scope 内（规范化路径比较）
    - 空可见 + 空选中 → []
    - 保持输入顺序、按规范化键去重；跳过空路径
    """
    if selected_paths:
        base: Sequence[str] = selected_paths
    else:
        base = visible_paths or []

    scope_keys = None
    if scope_paths is not None:
        scope_keys = {path_status_key(p) for p in scope_paths if p}

    out: List[str] = []
    seen = set()
    for p in base:
        if not p:
            continue
        key = path_status_key(p)
        if not key or key in seen:
            continue
        if scope_keys is not None and key not in scope_keys:
            continue
        out.append(p)
        seen.add(key)
    return out