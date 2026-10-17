"""整理方案第一切片的纯逻辑模型。

本模块只描述尚未执行的整理方案，不负责持久化，也不执行文件操作。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Optional, Tuple
from uuid import uuid4


PHYSICAL_MIGRATION = "physical_migration"


def _path_key(path: str) -> str:
    """使用平台路径规则比较排除目标，避免 Windows 大小写/斜杠差异。"""
    return os.path.normcase(os.path.normpath(str(path)))


@dataclass(frozen=True)
class PlanItem:
    """整理方案中的一项文件操作。"""

    source_path: str
    target_path: str
    requested_target_path: str = ""
    action_type: str = PHYSICAL_MIGRATION
    status: str = "planned"
    risk_flags: Tuple[str, ...] = ()
    target_conflict: bool = False
    external_change: Optional[bool] = None
    excluded: bool = False
    exclusion_reason: str = ""

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
        *,
        action_type: str = PHYSICAL_MIGRATION,
    ) -> "PlanItem":
        return cls(
            source_path=str(value.get("source_path") or value.get("old_path") or ""),
            target_path=str(value.get("target_path") or value.get("new_path") or ""),
            requested_target_path=str(
                value.get("requested_target_path")
                or value.get("target_path")
                or value.get("new_path")
                or ""
            ),
            action_type=str(value.get("action_type") or action_type),
            status=str(value.get("status") or "planned"),
            risk_flags=tuple(str(flag) for flag in (value.get("risk_flags") or ())),
            target_conflict=bool(value.get("target_conflict", False)),
            external_change=value.get("external_change"),
            excluded=bool(value.get("excluded", False)),
            exclusion_reason=str(value.get("exclusion_reason") or ""),
        )

    def exclude(self, reason: str = "用户排除") -> "PlanItem":
        return replace(self, excluded=True, exclusion_reason=reason)


@dataclass(frozen=True)
class OrganizationPlan:
    """一次整理方案的只读快照。

    方案可在确认前通过返回新对象的方式排除素材或操作类型；原对象不变，
    因此 GUI 摘要和确认使用的对象可以明确保持一致。本轮不写入数据库。
    """

    plan_id: str
    created_at: str
    work_scope_paths: Tuple[str, ...]
    target_paths: Tuple[str, ...]
    filter_summary: str
    items: Tuple[PlanItem, ...]

    @classmethod
    def from_migration_plan(
        cls,
        migration_plan: Iterable[Mapping[str, Any]],
        *,
        work_scope_paths: Iterable[str] = (),
        target_paths: Optional[Iterable[str]] = None,
        action_type: str = PHYSICAL_MIGRATION,
        filter_summary: str = "未筛选",
    ) -> "OrganizationPlan":
        items = tuple(
            PlanItem.from_mapping(item, action_type=action_type)
            for item in migration_plan
        )
        resolved_targets = tuple(target_paths) if target_paths is not None else tuple(
            item.source_path for item in items
        )
        return cls(
            plan_id=uuid4().hex,
            created_at=datetime.now(timezone.utc).isoformat(),
            work_scope_paths=tuple(work_scope_paths),
            target_paths=resolved_targets,
            filter_summary=filter_summary,
            items=items,
        )

    def exclude_paths(
        self,
        paths: Iterable[str],
        *,
        reason: str = "用户排除素材",
    ) -> "OrganizationPlan":
        excluded = {_path_key(path) for path in paths if path}
        return replace(
            self,
            items=tuple(
                item.exclude(reason)
                if _path_key(item.source_path) in excluded and not item.excluded
                else item
                for item in self.items
            ),
        )

    def exclude_action_types(
        self,
        action_types: Iterable[str],
        *,
        reason: str = "用户排除操作类型",
    ) -> "OrganizationPlan":
        excluded = {str(action_type) for action_type in action_types if action_type}
        return replace(
            self,
            items=tuple(
                item.exclude(reason)
                if item.action_type in excluded and not item.excluded
                else item
                for item in self.items
            ),
        )

    @property
    def active_items(self) -> Tuple[PlanItem, ...]:
        return tuple(item for item in self.items if not item.excluded)

    @property
    def active_source_paths(self) -> Tuple[str, ...]:
        return tuple(item.source_path for item in self.active_items)

    @property
    def excluded_items(self) -> Tuple[PlanItem, ...]:
        return tuple(item for item in self.items if item.excluded)

    def execution_summary(self, succeeded_count: int) -> dict[str, int]:
        """汇总当前会话的执行计数；未成功数不推断具体失败原因。"""
        requested = len(self.active_items)
        succeeded = int(succeeded_count)
        if succeeded < 0 or succeeded > requested:
            raise ValueError(
                f"成功数必须在 0 到确认执行数之间（收到 {succeeded}，确认执行 {requested}）"
            )
        return {
            "requested_count": requested,
            "succeeded_count": succeeded,
            "unresolved_count": requested - succeeded,
            "excluded_count": len(self.excluded_items),
        }

    def summary(self) -> dict[str, Any]:
        """返回确认框需要的可观察计数，不包含文件写入结果。"""
        by_action: dict[str, int] = {}
        for item in self.active_items:
            by_action[item.action_type] = by_action.get(item.action_type, 0) + 1
        return {
            "plan_id": self.plan_id,
            "work_scope_paths": self.work_scope_paths,
            "work_scope_count": len(self.work_scope_paths),
            "target_paths": self.target_paths,
            "target_count": len(self.target_paths),
            "total_items": len(self.items),
            "active_items": len(self.active_items),
            "excluded_items": len(self.excluded_items),
            "active_source_paths": self.active_source_paths,
            "active_by_action": by_action,
            "filter_summary": self.filter_summary,
        }
