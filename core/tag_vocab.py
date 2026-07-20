# -*- coding: utf-8 -*-
"""分析用词表子集 + 词表冷启动（纯逻辑，可注入聚类）。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from core.tag_normalize import (
    DEFAULT_CLOSED_GROUP_IDS,
    DEFAULT_SUGGESTION_GROUP_ID,
    GroupSpec,
    tag_name_from_pool_item,
)

# 精瘦默认（产品 B 档）
DEFAULT_PER_CLOSED_MIN = 15
DEFAULT_PER_CLOSED_MAX = 25
DEFAULT_TOTAL_MAX = 100

# 占位测试词：测试* / test*
PLACEHOLDER_RE = re.compile(r"^(测试|test)[\w\u4e00-\u9fff]*$", re.IGNORECASE)


@dataclass
class VocabSubsetResult:
    """分析用词表子集。"""

    by_group: Dict[str, List[str]]  # group_id -> 标准词列表（已裁剪）
    total_count: int
    warnings: List[str] = field(default_factory=list)
    is_thin: bool = False  # 空库或几乎全是占位
    placeholder_count: int = 0


def is_placeholder_tag(name: str) -> bool:
    n = (name or "").strip()
    if not n:
        return True
    if PLACEHOLDER_RE.match(n):
        return True
    if n.startswith("测试") or n.lower().startswith("test"):
        return True
    return False


def build_analysis_vocab_subset(
    group_standards: Mapping[str, Sequence[str]],
    *,
    group_meta: Optional[Mapping[str, Mapping[str, Any]]] = None,
    usage_counts: Optional[Mapping[str, int]] = None,
    explicit_order: Optional[Mapping[str, Sequence[str]]] = None,
    per_closed_max: int = DEFAULT_PER_CLOSED_MAX,
    total_max: int = DEFAULT_TOTAL_MAX,
    closed_ids: Optional[Set[str]] = None,
    suggestion_id: str = DEFAULT_SUGGESTION_GROUP_ID,
    exclude_aliases: Optional[Set[str]] = None,
    exclude_pending: Optional[Set[str]] = None,
) -> VocabSubsetResult:
    """
    构建分析用词表子集：只含标准词；排除别名与待审词；按组裁剪。

    裁剪优先级：组内 explicit_order > usage_count 降序 > 字典序。
    建议组可不塞满（分析时可选给参考）；封闭组严格裁到 per_closed_max。
    """
    closed_ids = set(closed_ids or DEFAULT_CLOSED_GROUP_IDS)
    usage_counts = dict(usage_counts or {})
    exclude_aliases = set(exclude_aliases or set())
    exclude_pending = set(exclude_pending or set())
    group_meta = group_meta or {}
    explicit_order = explicit_order or {}

    warnings: List[str] = []
    by_group: Dict[str, List[str]] = {}
    placeholder_count = 0
    real_standard_total = 0

    # 先处理封闭组，再建议组
    ordered_ids = [gid for gid in group_standards.keys() if gid in closed_ids]
    ordered_ids += [gid for gid in group_standards.keys() if gid not in closed_ids]

    remaining_budget = total_max

    for gid in ordered_ids:
        raw_list = list(group_standards.get(gid) or [])
        # 去重、剔空、剔别名/待审/占位（占位计数但不进子集）
        seen: Set[str] = set()
        cleaned: List[str] = []
        for name in raw_list:
            n = str(name or "").strip()
            if not n or n in seen:
                continue
            seen.add(n)
            if n in exclude_aliases or n in exclude_pending:
                continue
            if is_placeholder_tag(n):
                placeholder_count += 1
                continue
            cleaned.append(n)
            real_standard_total += 1

        order = list(explicit_order.get(gid) or [])
        order_index = {t: i for i, t in enumerate(order)}

        def sort_key(t: str) -> Tuple[int, int, str]:
            oi = order_index.get(t, 10_000)
            uc = -int(usage_counts.get(t, 0))
            return (oi, uc, t)

        cleaned.sort(key=sort_key)

        is_closed = gid in closed_ids or not (
            group_meta.get(gid, {}).get("ai_expandable", gid == suggestion_id)
        )
        if is_closed:
            cap = min(per_closed_max, remaining_budget if remaining_budget > 0 else 0)
        else:
            # 建议组：提示里可少放参考词，默认最多 10 且受总预算限制
            cap = min(10, per_closed_max, remaining_budget if remaining_budget > 0 else 0)

        selected = cleaned[: max(0, cap)]
        by_group[gid] = selected
        remaining_budget -= len(selected)

    total_count = sum(len(v) for v in by_group.values())
    closed_real = sum(
        1
        for gid, tags in by_group.items()
        if gid in closed_ids
        for _ in tags
    )
    is_thin = real_standard_total == 0 or (
        placeholder_count > 0 and closed_real == 0
    )
    if is_thin:
        warnings.append(
            "分析用词表过瘦或仅含占位词：请先完成词表冷启动并终审写入标准词。"
        )
    if placeholder_count > 0:
        warnings.append(f"已跳过 {placeholder_count} 个占位/测试标签，未送入分析候选。")
    if remaining_budget < 0:
        warnings.append("词表已按合计上限裁剪。")

    return VocabSubsetResult(
        by_group=by_group,
        total_count=total_count,
        warnings=warnings,
        is_thin=is_thin,
        placeholder_count=placeholder_count,
    )


def standards_map_from_tag_config(tag_config: Optional[Mapping[str, Any]]) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    if not tag_config:
        return out
    for group in tag_config.get("tag_groups") or []:
        gid = str(group.get("id") or "").strip()
        if not gid:
            continue
        names = []
        for t in group.get("tags") or []:
            n = tag_name_from_pool_item(t)
            if n:
                names.append(n)
        out[gid] = names
    return out


# ---------- 词表冷启动 ----------


@dataclass
class ColdStartCluster:
    standard: str
    aliases: List[str] = field(default_factory=list)
    group_id: str = DEFAULT_SUGGESTION_GROUP_ID


@dataclass
class ColdStartDraft:
    """冷启动草案（未写入库）。"""

    clusters: List[ColdStartCluster]
    by_group: Dict[str, List[str]]  # 裁剪后每组标准词
    alias_map: Dict[str, str]  # alias -> standard
    skipped_placeholders: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


ClusterFn = Callable[[Sequence[str]], List[ColdStartCluster]]


def default_rule_cluster(
    words: Sequence[str],
    *,
    closed_ids: Optional[Sequence[str]] = None,
) -> List[ColdStartCluster]:
    """
    可注入的默认近义分簇（无模型）：
    - 去重后每词一簇
    - 简单前缀/包含关系：短词作标准、长词作别名（或反过来取较短为标准）
    - 默认全进 custom，调用方可再映射
    """
    closed_ids = list(closed_ids or list(DEFAULT_CLOSED_GROUP_IDS) + [DEFAULT_SUGGESTION_GROUP_ID])
    uniq: List[str] = []
    seen: Set[str] = set()
    for w in words:
        t = str(w or "").strip()
        if not t or t in seen:
            continue
        seen.add(t)
        uniq.append(t)

    used: Set[str] = set()
    clusters: List[ColdStartCluster] = []
    # 按长度升序，便于短词做标准
    for w in sorted(uniq, key=lambda x: (len(x), x)):
        if w in used:
            continue
        aliases = []
        for other in uniq:
            if other == w or other in used:
                continue
            if w in other or other in w:
                # 较短为标准
                if len(other) < len(w):
                    continue
                aliases.append(other)
                used.add(other)
        used.add(w)
        clusters.append(
            ColdStartCluster(standard=w, aliases=aliases, group_id=DEFAULT_SUGGESTION_GROUP_ID)
        )
    return clusters


def heuristic_assign_group(word: str) -> str:
    """极简归组启发（无模型时用）；无法判断则 custom。"""
    w = word
    mood_kw = ("治愈", "紧张", "唯美", "开心", "难过", "焦虑", "幸福", "快乐", "安静", "压抑")
    subject_kw = ("人", "男", "女", "儿童", "老人", "学生", "动物", "猫", "狗")
    location_kw = ("城", "街", "海", "山", "湖", "河", "室内", "室外", "学校", "家", "办公", "天空")
    action_kw = ("跑", "走", "看", "读", "写", "说", "吃", "工作", "学习", "通勤", "健身")
    if any(k in w for k in mood_kw):
        return "mood"
    if any(k in w for k in subject_kw):
        return "subject"
    if any(k in w for k in location_kw):
        return "location"
    if any(k in w for k in action_kw):
        return "action"
    return DEFAULT_SUGGESTION_GROUP_ID


def build_cold_start_draft(
    draft_lines: Sequence[str],
    *,
    cluster_fn: Optional[ClusterFn] = None,
    per_closed_max: int = DEFAULT_PER_CLOSED_MAX,
    assign_group_fn: Optional[Callable[[str], str]] = None,
) -> ColdStartDraft:
    """
    从草稿行生成冷启动草案。不写库。
    cluster_fn 可注入（测试用假实现）；默认 default_rule_cluster。
    """
    skipped: List[str] = []
    words: List[str] = []
    for line in draft_lines:
        t = str(line or "").strip()
        if not t:
            continue
        if is_placeholder_tag(t):
            skipped.append(t)
            continue
        words.append(t)

    cluster_fn = cluster_fn or (lambda ws: default_rule_cluster(ws))
    assign_group_fn = assign_group_fn or heuristic_assign_group

    clusters = cluster_fn(words)
    # 归组 + 别名表
    alias_map: Dict[str, str] = {}
    by_group_all: Dict[str, List[str]] = {
        gid: [] for gid in list(DEFAULT_CLOSED_GROUP_IDS) + [DEFAULT_SUGGESTION_GROUP_ID]
    }
    for c in clusters:
        std = (c.standard or "").strip()
        if not std or is_placeholder_tag(std):
            if std:
                skipped.append(std)
            continue
        gid = (c.group_id or "").strip() or assign_group_fn(std)
        if gid not in by_group_all:
            by_group_all[gid] = []
        if std not in by_group_all[gid]:
            by_group_all[gid].append(std)
        c.group_id = gid
        for a in c.aliases:
            a = str(a or "").strip()
            if a and a != std:
                alias_map[a] = std

    # 裁剪封闭组
    by_group: Dict[str, List[str]] = {}
    for gid, tags in by_group_all.items():
        tags_sorted = sorted(set(tags), key=lambda x: x)
        if gid in DEFAULT_CLOSED_GROUP_IDS:
            by_group[gid] = tags_sorted[:per_closed_max]
        else:
            by_group[gid] = tags_sorted[: per_closed_max * 2]

    notes = []
    if skipped:
        notes.append(f"跳过占位词 {len(skipped)} 个")
    if not any(by_group.get(g) for g in DEFAULT_CLOSED_GROUP_IDS):
        notes.append("封闭组草案仍空：请检查草稿词表或调整归组")

    return ColdStartDraft(
        clusters=clusters,
        by_group=by_group,
        alias_map=alias_map,
        skipped_placeholders=skipped,
        notes=notes,
    )


def merge_draft_into_tag_config(
    tag_config: Dict[str, Any],
    draft: ColdStartDraft,
    *,
    replace_placeholders: bool = True,
    replace_all_group_tags: bool = False,
) -> Dict[str, Any]:
    """
    将草案合并进 tag_config 副本（不落盘）。
    - replace_placeholders：去掉测试* 占位
    - replace_all_group_tags：True 时整组替换为草案（显式）；False 时合并追加
    """
    import copy

    cfg = copy.deepcopy(tag_config or {"tag_groups": []})
    groups = cfg.setdefault("tag_groups", [])
    by_id = {g.get("id"): g for g in groups}

    for gid, standards in draft.by_group.items():
        if gid not in by_id:
            # 不自动创建未知组
            continue
        g = by_id[gid]
        existing_raw = g.get("tags") or []
        existing_names = [tag_name_from_pool_item(t) for t in existing_raw]
        existing_names = [n for n in existing_names if n]

        if replace_placeholders:
            existing_names = [n for n in existing_names if not is_placeholder_tag(n)]

        if replace_all_group_tags:
            merged = list(standards)
        else:
            merged = list(existing_names)
            for s in standards:
                if s not in merged:
                    merged.append(s)

        # 封闭组裁剪
        if gid in DEFAULT_CLOSED_GROUP_IDS:
            merged = merged[:DEFAULT_PER_CLOSED_MAX]
        g["tags"] = merged

    return cfg


def commit_plan_summary(draft: ColdStartDraft) -> Dict[str, Any]:
    """供 UI/测试观察的 commit 摘要。"""
    return {
        "groups": {k: list(v) for k, v in draft.by_group.items()},
        "alias_count": len(draft.alias_map),
        "aliases": dict(draft.alias_map),
        "skipped_placeholders": list(draft.skipped_placeholders),
        "notes": list(draft.notes),
    }