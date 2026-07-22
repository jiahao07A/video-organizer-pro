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

# 词.txt 别名行：标准词 ← 别名1, 别名2（兼容 ASCII: <-）
_ALIAS_ARROW_RE = re.compile(
    r"^(?P<std>.+?)\s*(?:←|<-)\s*(?P<aliases>.+)$"
)
_ALIAS_SPLIT_RE = re.compile(r"[,，、/|]+")

# 词.txt 分组标题：# ========== 氛围 mood ==========
_GROUP_HEADER_RE = re.compile(
    r"#\s*=+\s*.*?\b(?P<gid>mood|subject|location|action|custom)\b",
    re.IGNORECASE,
)


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


def parse_vocab_draft_line(line: str) -> Optional[Tuple[str, List[str]]]:
    """
    解析词表草稿一行。

    支持：
    - ``标准词``
    - ``标准词 ← 别名1, 别名2``（兼容 ``<-``）

    返回 (标准词, 别名列表)；空行/注释返回 None。
    """
    t = str(line or "").strip()
    if not t or t.startswith("#"):
        return None

    m = _ALIAS_ARROW_RE.match(t)
    if m:
        std = m.group("std").strip()
        raw_aliases = m.group("aliases").strip()
        aliases: List[str] = []
        seen: Set[str] = set()
        for part in _ALIAS_SPLIT_RE.split(raw_aliases):
            a = part.strip()
            if not a or a == std or a in seen:
                continue
            seen.add(a)
            aliases.append(a)
        if not std:
            return None
        return std, aliases

    # 无箭头：整行即标准词（忽略误写的尾部空白）
    return t, []


def parse_vocab_group_header(line: str) -> Optional[str]:
    """
    解析词表分组标题行，如 ``# ========== 氛围 mood ==========``。
    返回 group_id（小写）；非标题返回 None。
    """
    t = str(line or "").strip()
    if not t.startswith("#"):
        return None
    m = _GROUP_HEADER_RE.search(t)
    if not m:
        return None
    return m.group("gid").strip().lower()


def build_vocab_draft_from_lines(draft_lines: Sequence[str]) -> ColdStartDraft:
    """
    直接按词表文件语义加载，不做 AI/规则分簇、不做精瘦裁剪。

    约定（与 ``词.txt`` 一致）：
    - ``#`` 注释；``# === ... mood/subject/... ===`` 切换当前标签组
    - ``标准词`` 或 ``标准词 ← 别名1, 别名2``
    """
    current_gid = DEFAULT_SUGGESTION_GROUP_ID
    clusters: List[ColdStartCluster] = []
    alias_map: Dict[str, str] = {}
    by_group: Dict[str, List[str]] = {
        gid: [] for gid in list(DEFAULT_CLOSED_GROUP_IDS) + [DEFAULT_SUGGESTION_GROUP_ID]
    }
    claimed: Set[str] = set()
    notes: List[str] = ["直接加载词表（无分簇/无裁剪）。"]
    skipped: List[str] = []
    group_hits = 0

    for line in draft_lines:
        raw = str(line or "")
        header = parse_vocab_group_header(raw)
        if header:
            current_gid = header
            group_hits += 1
            if header not in by_group:
                by_group[header] = []
            continue

        parsed = parse_vocab_draft_line(raw)
        if parsed is None:
            continue
        std, aliases = parsed
        if not std:
            continue

        if std in claimed:
            for a in aliases:
                if a and a != std and a not in claimed:
                    alias_map[a] = std
                    claimed.add(a)
                    for c in clusters:
                        if c.standard == std and a not in c.aliases:
                            c.aliases.append(a)
                            break
            continue

        clean_aliases: List[str] = []
        for a in aliases:
            if not a or a == std or a in claimed:
                continue
            clean_aliases.append(a)
            claimed.add(a)
            alias_map[a] = std

        claimed.add(std)
        clusters.append(
            ColdStartCluster(standard=std, aliases=clean_aliases, group_id=current_gid)
        )
        if std not in by_group[current_gid]:
            by_group[current_gid].append(std)

    notes.append(
        f"解析到分组标题 {group_hits} 处，标准词 {len(clusters)} 个，别名 {len(alias_map)} 个。"
    )
    if group_hits == 0:
        notes.append(
            "未识别到 mood/subject/location/action/custom 分组标题，词已落入建议组 custom。"
        )

    return ColdStartDraft(
        clusters=clusters,
        by_group=by_group,
        alias_map=alias_map,
        skipped_placeholders=skipped,
        notes=notes,
    )


def build_cold_start_draft(
    draft_lines: Sequence[str],
    *,
    cluster_fn: Optional[ClusterFn] = None,
    per_closed_max: int = DEFAULT_PER_CLOSED_MAX,
    assign_group_fn: Optional[Callable[[str], str]] = None,
) -> ColdStartDraft:
    """
    从草稿行生成冷启动草案（可聚类路径，保留给测试）。
    产品默认请用 ``build_vocab_draft_from_lines`` 直接加载词.txt。
    """
    skipped: List[str] = []
    # 文件内已写明的标准词 → 别名（优先于自动分簇）
    explicit: Dict[str, List[str]] = {}
    plain_words: List[str] = []
    claimed: Set[str] = set()  # 已作为标准词或别名出现的写法

    for line in draft_lines:
        if parse_vocab_group_header(line):
            continue
        parsed = parse_vocab_draft_line(line)
        if parsed is None:
            continue
        std, aliases = parsed
        if is_placeholder_tag(std):
            skipped.append(std)
            continue
        clean_aliases = [a for a in aliases if not is_placeholder_tag(a)]
        for a in aliases:
            if is_placeholder_tag(a):
                skipped.append(a)

        if std in claimed:
            # 同标准词重复行：合并别名
            if std in explicit:
                for a in clean_aliases:
                    if a not in claimed and a not in explicit[std]:
                        explicit[std].append(a)
                        claimed.add(a)
            continue

        if clean_aliases:
            explicit[std] = list(clean_aliases)
            claimed.add(std)
            for a in clean_aliases:
                claimed.add(a)
        else:
            plain_words.append(std)

    # 纯词去重，且不与已声明标准/别名冲突
    plain_uniq: List[str] = []
    for w in plain_words:
        if w in claimed:
            continue
        if w in plain_uniq:
            continue
        plain_uniq.append(w)
        claimed.add(w)

    cluster_fn = cluster_fn or (lambda ws: default_rule_cluster(ws))
    assign_group_fn = assign_group_fn or heuristic_assign_group

    auto_clusters = cluster_fn(plain_uniq) if plain_uniq else []
    # 显式别名簇优先，再拼自动簇（自动簇里若标准词已被占用则跳过）
    clusters: List[ColdStartCluster] = []
    used_std: Set[str] = set()
    for std, aliases in explicit.items():
        clusters.append(ColdStartCluster(standard=std, aliases=list(aliases), group_id=""))
        used_std.add(std)
    for c in auto_clusters:
        std = (c.standard or "").strip()
        if not std or std in used_std:
            continue
        # 别名不得指向已占用词
        safe_aliases = [
            a for a in (c.aliases or [])
            if a and a != std and a not in used_std and a not in explicit
        ]
        clusters.append(
            ColdStartCluster(standard=std, aliases=safe_aliases, group_id=c.group_id or "")
        )
        used_std.add(std)

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
    if explicit:
        notes.append(f"显式别名行 {len(explicit)} 条")
    if not any(by_group.get(g) for g in DEFAULT_CLOSED_GROUP_IDS):
        notes.append("封闭组草案仍空：请检查草稿词表或调整归组")

    return ColdStartDraft(
        clusters=clusters,
        by_group=by_group,
        alias_map=alias_map,
        skipped_placeholders=skipped,
        notes=notes,
    )


def default_tag_group_template(group_id: str) -> Dict[str, Any]:
    """词表写入时若配置中尚无该组，自动创建默认骨架。"""
    names = {
        "mood": "氛围",
        "subject": "主体",
        "location": "场景",
        "action": "动作",
        "custom": "建议",
    }
    closed = group_id in DEFAULT_CLOSED_GROUP_IDS
    return {
        "id": group_id,
        "name": names.get(group_id, group_id),
        "rules": {
            "selection_mode": "single" if closed else "multiple",
            "max_count": 1 if closed else 5,
            "ai_expandable": not closed,
            "local_prompt": "",
        },
        "tags": [],
    }


def ensure_canonical_tag_groups(tag_config: Dict[str, Any]) -> Dict[str, Any]:
    """保证 tag_config 至少含 mood/subject/location/action/custom 五组。"""
    import copy

    cfg = copy.deepcopy(tag_config or {})
    groups = cfg.setdefault("tag_groups", [])
    by_id = {g.get("id"): g for g in groups if isinstance(g, dict)}
    order = list(DEFAULT_CLOSED_GROUP_IDS) + [DEFAULT_SUGGESTION_GROUP_ID]
    for gid in order:
        if gid not in by_id:
            g = default_tag_group_template(gid)
            groups.append(g)
            by_id[gid] = g
    return cfg


def merge_draft_into_tag_config(
    tag_config: Dict[str, Any],
    draft: ColdStartDraft,
    *,
    replace_placeholders: bool = True,
    replace_all_group_tags: bool = False,
    cap_closed_groups: bool = False,
    create_missing_groups: bool = True,
) -> Dict[str, Any]:
    """
    将草案合并进 tag_config 副本（不落盘）。
    - replace_placeholders：去掉测试* 占位
    - replace_all_group_tags：True 时整组替换为草案（显式）；False 时合并追加
    - cap_closed_groups：True 时封闭组截到精瘦上限（旧冷启动用）；直接加载词.txt 应为 False
    - create_missing_groups：True 时为草案中的组自动创建默认标签组（修复 tag_groups 为空写不进去）
    """
    import copy

    cfg = ensure_canonical_tag_groups(tag_config or {})
    groups = cfg.setdefault("tag_groups", [])
    by_id = {g.get("id"): g for g in groups if isinstance(g, dict)}

    for gid, standards in (draft.by_group or {}).items():
        if not gid:
            continue
        if gid not in by_id:
            if not create_missing_groups:
                continue
            g = default_tag_group_template(gid)
            groups.append(g)
            by_id[gid] = g
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

        # 仅在显式要求时裁剪封闭组（直接加载完整词表时不要裁）
        if cap_closed_groups and gid in DEFAULT_CLOSED_GROUP_IDS:
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