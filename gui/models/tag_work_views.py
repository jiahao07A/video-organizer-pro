# -*- coding: utf-8 -*-
"""标签库工作视图的纯逻辑（不依赖 Qt / 像素）。

拆成两个工作视图共用的可测逻辑：

- 待审审核视图：搜索 / 按建议组筛选 / 排序，以及把批量 AI 建议映射为可勾选行。
- 标准词管理视图：把 tag_config 与库详情合成标准词行（标准词 / 别名 / 使用次数 / 所属组）。

保持服务层人工确认边界：这里只做只读的数据整形，不写库。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence


# --- 待审审核视图 ---------------------------------------------------------


def filter_pending_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    query: str = "",
    group_id: str = "",
) -> List[Dict[str, Any]]:
    """按原文关键字与建议组筛选待审词（只读）。

    query 为大小写不敏感的子串匹配（原文 / 建议组名都参与）；
    group_id 为空表示不过滤；``__none__`` 表示只看无建议组的行。
    """
    q = (query or "").strip().lower()
    gid = (group_id or "").strip()
    out: List[Dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, Mapping):
            continue
        raw = str(row.get("raw_text") or "")
        src = str(row.get("group_id") or "")
        if gid:
            if gid == "__none__":
                if src:
                    continue
            elif src.lower() != gid.lower():
                continue
        if q:
            haystack = f"{raw} {src}".lower()
            if q not in haystack:
                continue
        out.append(dict(row))
    return out


@dataclass
class PendingSuggestionRow:
    """一条批量 AI 建议在待审视图中的呈现行（默认不勾选）。"""

    suggestion: Any
    raw_text: str = ""
    action: str = ""
    action_label: str = ""
    source: str = "rule"
    reason: str = ""
    recommended_standard: str = ""
    recommended_group_id: str = ""
    checked: bool = False

    @property
    def source_label(self) -> str:
        return "模型" if self.source == "model" else "规则"

    @property
    def display(self) -> str:
        return f"[{self.source_label}] {self.raw_text}  ·  {self.action_label}  ·  {self.reason}"


def pending_suggestion_action_label(sug: Any) -> str:
    """把建议动作翻译成待审视图里的中文标签。"""
    action = getattr(sug, "action", "") or ""
    if action == "link_alias":
        return f"挂别名→{getattr(sug, 'recommended_standard', '') or ''}"
    if action == "approve_standard":
        gid = (
            getattr(sug, "recommended_group_id", None)
            or getattr(sug, "group_id", "")
            or ""
        )
        return f"批准→{gid}"
    return "丢弃"


def build_pending_suggestion_rows(
    suggestions: Iterable[Any],
) -> List[PendingSuggestionRow]:
    """把批量建议转换成默认不勾选的待审视图行。"""
    out: List[PendingSuggestionRow] = []
    for sug in suggestions or []:
        out.append(
            PendingSuggestionRow(
                suggestion=sug,
                raw_text=str(getattr(sug, "raw_text", "") or ""),
                action=str(getattr(sug, "action", "") or ""),
                action_label=pending_suggestion_action_label(sug),
                source=str(getattr(sug, "source", "rule") or "rule"),
                reason=str(getattr(sug, "reason", "") or ""),
                recommended_standard=str(
                    getattr(sug, "recommended_standard", "") or ""
                ),
                recommended_group_id=str(
                    getattr(sug, "recommended_group_id", None)
                    or getattr(sug, "group_id", "")
                    or ""
                ),
                checked=False,
            )
        )
    return out


def selected_suggestions(rows: Iterable[PendingSuggestionRow]) -> List[Any]:
    """仅返回被明确勾选的建议（未选不写库的服务层边界在此收敛）。"""
    return [r.suggestion for r in rows or [] if getattr(r, "checked", False)]


# --- 标准词管理视图 -------------------------------------------------------


@dataclass
class StandardWordRow:
    """标准词管理视图的一行：标准词 / 别名 / 使用次数 / 所属组。"""

    name: str
    group_id: str = ""
    group_name: str = ""
    usage_count: int = 0
    aliases: List[str] = field(default_factory=list)
    color: Optional[str] = None
    tag_id: Optional[int] = None

    @property
    def alias_text(self) -> str:
        return "、".join(self.aliases)

    @property
    def display(self) -> str:
        return self.name


def _names_from_group_tags(tags: Iterable[Any]) -> List[str]:
    names: List[str] = []
    for t in tags or []:
        if isinstance(t, Mapping):
            n = t.get("name")
        else:
            n = t
        n = str(n).strip() if n is not None else ""
        if n and n not in names:
            names.append(n)
    return names


def group_name_map(tag_config: Optional[Mapping[str, Any]]) -> Dict[str, str]:
    """组 id → 显示名（大小写不敏感，pool 排除）。"""
    out: Dict[str, str] = {}
    for g in (tag_config or {}).get("tag_groups") or []:
        if not isinstance(g, Mapping):
            continue
        gid = str(g.get("id") or "").strip()
        if not gid or gid.lower() == "pool":
            continue
        out[gid] = str(g.get("name") or gid)
    return out


def build_standard_word_rows(
    tag_config: Optional[Mapping[str, Any]],
    tags_detail: Optional[Sequence[Mapping[str, Any]]] = None,
    synonyms: Optional[Mapping[str, str]] = None,
) -> List[StandardWordRow]:
    """合成标准词管理行：以 tag_config 分组的标准词为准，补详情与别名。

    只保留合法标签组（pool / 无组词不展示为标准词）。
    """
    names_to_gid: Dict[str, str] = {}
    for g in (tag_config or {}).get("tag_groups") or []:
        if not isinstance(g, Mapping):
            continue
        gid = str(g.get("id") or "").strip()
        if not gid or gid.lower() == "pool":
            continue
        for name in _names_from_group_tags(g.get("tags")):
            names_to_gid.setdefault(name, gid)

    detail_map: Dict[str, Mapping[str, Any]] = {}
    for d in tags_detail or []:
        if not isinstance(d, Mapping):
            continue
        n = str(d.get("tag_name") or "").strip()
        if n:
            detail_map[n] = d

    gnames = group_name_map(tag_config)

    alias_map: Dict[str, List[str]] = {}
    for alias, std in (synonyms or {}).items():
        std = str(std or "").strip()
        if not std:
            continue
        alias_map.setdefault(std, []).append(str(alias).strip())

    rows: List[StandardWordRow] = []
    for name, gid in names_to_gid.items():
        detail = detail_map.get(name) or {}
        rows.append(
            StandardWordRow(
                name=name,
                group_id=gid,
                group_name=gnames.get(gid, gid),
                usage_count=int(detail.get("usage_count") or 0),
                aliases=sorted(alias_map.get(name, [])),
                color=detail.get("color"),
                tag_id=detail.get("id"),
            )
        )
    rows.sort(key=lambda r: (r.group_id.lower(), -r.usage_count, r.name))
    return rows


def filter_standard_word_rows(
    rows: Sequence[StandardWordRow],
    *,
    query: str = "",
    group_id: str = "",
) -> List[StandardWordRow]:
    """按标准词 / 别名关键字与所属组筛选标准词行。"""
    q = (query or "").strip().lower()
    gid = (group_id or "").strip()
    out: List[StandardWordRow] = []
    for row in rows or []:
        if gid and row.group_id.lower() != gid.lower():
            continue
        if q:
            haystack = " ".join(
                [row.name, row.group_name, row.group_id, *row.aliases]
            ).lower()
            if q not in haystack:
                continue
        out.append(row)
    return out
