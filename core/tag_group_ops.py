# -*- coding: utf-8 -*-
"""标签组归属与中转池废除（S4）纯逻辑。

标准词必须属于某一标签组；pool / 未分组标准词降为待审；
删组须整组改派；导入/批准写入标准词须指定有效 group_id。
不触碰视频素材上的 tags 字符串。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple


POOL_GROUP_ID = "pool"


def known_group_ids(groups: Optional[Sequence[Mapping[str, Any]]]) -> Set[str]:
    """从 tag_groups 提取合法组 id（排除 pool）。"""
    out: Set[str] = set()
    for g in groups or []:
        if not isinstance(g, Mapping):
            continue
        gid = str(g.get("id") or "").strip()
        if gid and gid.lower() != POOL_GROUP_ID:
            out.add(gid)
    return out


def is_ungrouped_or_pool_dimension(
    dimension: Optional[str],
    known: Set[str],
) -> bool:
    """dimension 为 pool、空或非已知组 → 视为无合法组归属。"""
    dim = (dimension or "").strip()
    if not dim:
        return True
    key = dim.lower() if dim.lower() in {k.lower() for k in known} else dim
    # 大小写不敏感匹配 known
    known_lower = {k.lower(): k for k in known}
    if dim.lower() == POOL_GROUP_ID:
        return True
    if dim.lower() in known_lower or dim in known:
        return False
    return True


def validate_standard_tag_group_id(
    group_id: Optional[str],
    known_groups: Iterable[str],
) -> Tuple[bool, str]:
    """写入标准词时校验 group_id：缺省 / pool / 未知组 → 拒绝。"""
    gid = (group_id or "").strip()
    if not gid:
        return False, "缺少目标标签组 group_id"
    if gid.lower() == POOL_GROUP_ID:
        return False, "中转池已废除，不能作为标准词落点"
    known = {str(x).strip() for x in known_groups if str(x).strip()}
    known_lower = {k.lower(): k for k in known}
    if gid not in known and gid.lower() not in known_lower:
        return False, f"未知标签组: {gid}"
    return True, ""


@dataclass
class PoolMigrationResult:
    """pool/未分组标准词 → 待审 的纯逻辑结果。"""

    to_pending: List[str] = field(default_factory=list)
    remove_library_ids: List[int] = field(default_factory=list)
    remove_library_names: List[str] = field(default_factory=list)
    # 从 tag_config 各组 tags 中应剔除的词名
    strip_from_config: List[str] = field(default_factory=list)


def migrate_pool_tags_to_pending(
    tags_list: Sequence[Mapping[str, Any]],
    pending_list: Sequence[Mapping[str, Any]],
    known_groups: Iterable[str],
) -> PoolMigrationResult:
    """
    将 dimension 为 pool / 空 / 非已知组 的标准词标为应迁入待审。

    - 已在 pending 队列中的同名词仍从标准词表移除，但不重复加入 to_pending
    - 不修改视频素材 tags 字符串（调用方不得批量清空）
    """
    known = {str(x).strip() for x in known_groups if str(x).strip()}
    known.discard(POOL_GROUP_ID)
    known = {k for k in known if k.lower() != POOL_GROUP_ID}

    existing_pending = {
        str(p.get("raw_text") or "").strip()
        for p in pending_list or []
        if str(p.get("raw_text") or "").strip()
        and str(p.get("status") or "pending").strip() in ("", "pending")
    }
    # 若 status 字段缺失，也视为待审名占用
    for p in pending_list or []:
        rt = str(p.get("raw_text") or "").strip()
        st = str(p.get("status") or "pending").strip() or "pending"
        if rt and st == "pending":
            existing_pending.add(rt)

    result = PoolMigrationResult()
    seen_names: Set[str] = set()

    for row in tags_list or []:
        name = str(row.get("tag_name") or "").strip()
        if not name or name in seen_names:
            continue
        dim = row.get("dimension")
        if not is_ungrouped_or_pool_dimension(dim if dim is None else str(dim), known):
            continue
        seen_names.add(name)
        tid = row.get("id")
        if tid is not None:
            try:
                result.remove_library_ids.append(int(tid))
            except (TypeError, ValueError):
                pass
        result.remove_library_names.append(name)
        result.strip_from_config.append(name)
        if name not in existing_pending:
            result.to_pending.append(name)
            existing_pending.add(name)

    return result


def strip_names_from_tag_groups(
    groups: Sequence[Mapping[str, Any]],
    names: Iterable[str],
) -> List[Dict[str, Any]]:
    """从各组 tags 中移除指定词名，返回新 groups 列表（浅拷贝组 dict）。"""
    drop = {str(n).strip() for n in names if str(n).strip()}
    if not drop:
        return [dict(g) if isinstance(g, Mapping) else g for g in groups]  # type: ignore[misc]

    out: List[Dict[str, Any]] = []
    for g in groups or []:
        if not isinstance(g, Mapping):
            continue
        ng = dict(g)
        tags = ng.get("tags") or []
        kept: List[Any] = []
        for t in tags:
            if isinstance(t, dict):
                n = str(t.get("name") or "").strip()
            else:
                n = str(t or "").strip()
            if n and n not in drop:
                kept.append(t)
        ng["tags"] = kept
        out.append(ng)
    return out


@dataclass
class DeleteGroupVerdict:
    allowed: bool
    reason: str = ""
    tag_count: int = 0
    needs_reassign: bool = False


def _group_tag_names(group: Mapping[str, Any]) -> List[str]:
    names: List[str] = []
    for t in group.get("tags") or []:
        if isinstance(t, dict):
            n = str(t.get("name") or "").strip()
        else:
            n = str(t or "").strip()
        if n:
            names.append(n)
    return names


def can_delete_tag_group(
    groups: Sequence[Mapping[str, Any]],
    group_id: str,
    *,
    target_group_id: Optional[str] = None,
) -> DeleteGroupVerdict:
    """
    判断能否删除标签组。
    - 唯一剩余组：禁止
    - 空组：可直接删
    - 非空：必须提供有效且不同的 target_group_id
    """
    gid = (group_id or "").strip()
    if not gid:
        return DeleteGroupVerdict(False, "缺少 group_id")

    group_list = [g for g in (groups or []) if isinstance(g, Mapping) and g.get("id")]
    if len(group_list) <= 1:
        return DeleteGroupVerdict(False, "不能删除唯一剩余标签组", tag_count=0)

    source = next((g for g in group_list if str(g.get("id")) == gid), None)
    if source is None:
        return DeleteGroupVerdict(False, f"标签组不存在: {gid}")

    names = _group_tag_names(source)
    n = len(names)
    if n == 0:
        return DeleteGroupVerdict(True, "empty", tag_count=0, needs_reassign=False)

    tid = (target_group_id or "").strip()
    if not tid:
        return DeleteGroupVerdict(
            False,
            "非空标签组须指定 target_group_id 整组改派后再删",
            tag_count=n,
            needs_reassign=True,
        )
    if tid == gid:
        return DeleteGroupVerdict(False, "目标组不能与待删组相同", tag_count=n, needs_reassign=True)
    if tid.lower() == POOL_GROUP_ID:
        return DeleteGroupVerdict(False, "中转池已废除，不能作为改派目标", tag_count=n, needs_reassign=True)

    known = known_group_ids(group_list)
    ok, err = validate_standard_tag_group_id(tid, known)
    if not ok:
        return DeleteGroupVerdict(False, err or "无效目标组", tag_count=n, needs_reassign=True)

    return DeleteGroupVerdict(True, "reassign", tag_count=n, needs_reassign=True)


def reassign_and_remove_group(
    groups: Sequence[Mapping[str, Any]],
    source_group_id: str,
    target_group_id: Optional[str] = None,
) -> Tuple[bool, List[Dict[str, Any]], str, List[str]]:
    """
    整组改派（若非空）并删除源组。

    Returns:
        (ok, new_groups, error_message, moved_tag_names)
    """
    verdict = can_delete_tag_group(groups, source_group_id, target_group_id=target_group_id)
    if not verdict.allowed:
        return False, [dict(g) for g in groups if isinstance(g, Mapping)], verdict.reason, []

    sid = (source_group_id or "").strip()
    tid = (target_group_id or "").strip()
    new_groups: List[Dict[str, Any]] = []
    moved: List[str] = []
    source_tags: List[Any] = []

    for g in groups or []:
        if not isinstance(g, Mapping):
            continue
        ng = dict(g)
        if str(ng.get("id") or "") == sid:
            source_tags = list(ng.get("tags") or [])
            moved = _group_tag_names(ng)
            continue  # drop source
        new_groups.append(ng)

    if moved and tid:
        for g in new_groups:
            if str(g.get("id") or "") != tid:
                continue
            existing = _group_tag_names(g)
            existing_set = set(existing)
            tags = list(g.get("tags") or [])
            for t in source_tags:
                if isinstance(t, dict):
                    n = str(t.get("name") or "").strip()
                else:
                    n = str(t or "").strip()
                if n and n not in existing_set:
                    # 保留 dict 型标签元数据（name_en/icon 等）
                    tags.append(t if isinstance(t, dict) else n)
                    existing_set.add(n)
            g["tags"] = tags
            break

    return True, new_groups, "", moved


def reject_pool_in_classified(
    classified_tags: Mapping[str, Sequence[str]],
    known_groups: Iterable[str],
) -> Tuple[bool, str]:
    """导入落库前：拒绝 pool 或未知组作为标准词目标。"""
    known = {str(x).strip() for x in known_groups if str(x).strip()}
    for gid, tags in (classified_tags or {}).items():
        key = str(gid or "").strip()
        tag_list = [t for t in (tags or []) if str(t or "").strip()]
        if not tag_list:
            continue
        ok, err = validate_standard_tag_group_id(key, known)
        if not ok:
            return False, err or f"拒绝组 {key}"
    return True, ""