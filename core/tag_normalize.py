# -*- coding: utf-8 -*-
"""标签归一 / 分析用词表子集相关纯逻辑（主接缝）。

半封闭：封闭组只认标准词；建议组可保留库外词；别名→标准词；禁止模糊近邻贴词。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set


# 默认封闭组 / 建议组 id（与 tag_config 一致）
DEFAULT_CLOSED_GROUP_IDS = frozenset({"mood", "subject", "location", "action"})
DEFAULT_SUGGESTION_GROUP_ID = "custom"


@dataclass(frozen=True)
class GroupSpec:
    """单个标签组的归一规则。"""

    id: str
    max_count: int = 1
    ai_expandable: bool = False
    standards: frozenset = frozenset()  # 标准词集合


@dataclass
class PendingTag:
    """待审词（封闭组丢弃的库外原文）。"""

    raw_text: str
    group_id: str
    status: str = "pending"


@dataclass
class NormalizeResult:
    tags: List[str]
    tag_groups: Dict[str, List[str]]
    pending: List[PendingTag] = field(default_factory=list)


def tag_name_from_pool_item(item: Any) -> str:
    """从 tag_config 词条（str 或 dict）提取标准词名。"""
    if isinstance(item, dict):
        return str(item.get("name") or "").strip()
    return str(item or "").strip()


def group_specs_from_tag_config(tag_config: Optional[Mapping[str, Any]]) -> List[GroupSpec]:
    """从 tag_config['tag_groups'] 构建 GroupSpec 列表。"""
    if not tag_config:
        return []
    specs: List[GroupSpec] = []
    for group in tag_config.get("tag_groups") or []:
        gid = str(group.get("id") or "").strip()
        if not gid:
            continue
        rules = group.get("rules") or {}
        names = []
        for t in group.get("tags") or []:
            n = tag_name_from_pool_item(t)
            if n:
                names.append(n)
        specs.append(
            GroupSpec(
                id=gid,
                max_count=int(rules.get("max_count") or 1),
                ai_expandable=bool(rules.get("ai_expandable", False)),
                standards=frozenset(names),
            )
        )
    return specs


def resolve_to_standard(
    raw: str,
    alias_map: Mapping[str, str],
    *,
    standards: Optional[Set[str]] = None,
) -> Optional[str]:
    """
    将原文解析为标准词。
    - 已是标准词 → 返回自身
    - 是别名 → 返回映射的标准词
    - 否则 → None（不模糊猜测）
    若提供 standards，映射结果须落在 standards 内，否则 None。
    """
    t = (raw or "").strip()
    if not t:
        return None
    std: Optional[str] = None
    if standards is not None and t in standards:
        std = t
    elif t in alias_map:
        std = str(alias_map[t]).strip() or None
    elif standards is None:
        # 无组内标准集时：仅精确命中别名表，否则保留原文由调用方决定
        return t
    else:
        return None

    if std is None:
        return None
    if standards is not None and std not in standards:
        return None
    return std


def normalize_grouped_tags(
    raw_by_group: Mapping[str, Any],
    groups: Sequence[GroupSpec],
    alias_map: Optional[Mapping[str, str]] = None,
) -> NormalizeResult:
    """
    按组归一标签。

    封闭组（ai_expandable=False）：只保留标准词（含别名映射后）；库外进 pending 并丢弃。
    建议组（ai_expandable=True）：别名映射后可保留库外词；不自动进库。
    禁止将库外词贴到「最像」的标准词。
    """
    alias_map = dict(alias_map or {})
    by_id = {g.id: g for g in groups}
    tag_groups: Dict[str, List[str]] = {}
    pending: List[PendingTag] = []
    flat: List[str] = []
    seen_flat: Set[str] = set()

    # 处理配置中的组
    for spec in groups:
        raw_val = raw_by_group.get(spec.id)
        raw_list = _as_tag_list(raw_val)
        accepted: List[str] = []
        standards = set(spec.standards)

        for raw in raw_list:
            raw_s = str(raw).strip()
            if not raw_s:
                continue
            if not spec.ai_expandable:
                # 封闭组
                if not standards:
                    # 无标准词库：全部丢弃并待审（避免开放造词）
                    pending.append(PendingTag(raw_text=raw_s, group_id=spec.id))
                    continue
                resolved = resolve_to_standard(raw_s, alias_map, standards=standards)
                if resolved is None:
                    pending.append(PendingTag(raw_text=raw_s, group_id=spec.id))
                    continue
                if resolved not in accepted:
                    accepted.append(resolved)
            else:
                # 建议组：别名→标准词（若标准在全库任一处出现则用标准写法）；否则保留原文
                if raw_s in alias_map:
                    mapped = str(alias_map[raw_s]).strip()
                    pick = mapped or raw_s
                elif standards and raw_s in standards:
                    pick = raw_s
                else:
                    pick = raw_s
                if pick not in accepted:
                    accepted.append(pick)

        max_c = max(1, int(spec.max_count or 1))
        accepted = accepted[:max_c]
        tag_groups[spec.id] = accepted
        for t in accepted:
            if t not in seen_flat:
                seen_flat.add(t)
                flat.append(t)

    # 未在 GroupSpec 中声明、但出现在 raw 的维度：忽略（避免脏维度）
    return NormalizeResult(tags=flat, tag_groups=tag_groups, pending=pending)


def normalize_flat_tags(
    tags: Sequence[Any],
    alias_map: Optional[Mapping[str, str]] = None,
    *,
    known_standards: Optional[Set[str]] = None,
    drop_unknown: bool = False,
) -> List[str]:
    """
    扁平标签列表归一：别名→标准词、去空白去重。
    drop_unknown=True 且提供 known_standards 时丢弃未知词（用于强制封闭场景）。
    """
    alias_map = dict(alias_map or {})
    out: List[str] = []
    seen: Set[str] = set()
    for raw in tags:
        t = str(raw or "").strip()
        if not t:
            continue
        if t in alias_map:
            t = str(alias_map[t]).strip() or t
        if known_standards is not None and t not in known_standards:
            if drop_unknown:
                continue
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def expand_tags_for_filter(
    selected: Sequence[str],
    alias_map: Optional[Mapping[str, str]] = None,
) -> Set[str]:
    """
    筛选扩展：选中别名时同时命中标准词；选中标准词时同时命中其别名。
    视频落库应为标准词，故扩展筛选侧即可。
    """
    alias_map = dict(alias_map or {})
    reverse: Dict[str, Set[str]] = {}
    for alias, standard in alias_map.items():
        a = str(alias).strip()
        s = str(standard).strip()
        if not a or not s:
            continue
        reverse.setdefault(s, set()).add(a)

    expanded: Set[str] = set()
    for raw in selected:
        t = str(raw or "").strip()
        if not t:
            continue
        expanded.add(t)
        if t in alias_map:
            expanded.add(str(alias_map[t]).strip())
        if t in reverse:
            expanded.update(reverse[t])
    return expanded


def all_standards_from_groups(groups: Sequence[GroupSpec]) -> Set[str]:
    s: Set[str] = set()
    for g in groups:
        s |= set(g.standards)
    return s


def _as_tag_list(val: Any) -> List[str]:
    if val is None:
        return []
    if isinstance(val, str):
        return [val] if val.strip() else []
    if isinstance(val, (list, tuple)):
        return [str(x) for x in val]
    return [str(val)]