# -*- coding: utf-8 -*-
"""标签库 AI 辅助：待审建议 / 近义巡检（可注入假实现；确认前不写库）。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Set


@dataclass
class PendingTagSuggestion:
    pending_id: int
    raw_text: str
    group_id: str
    action: str  # approve_standard | link_alias | discard
    recommended_group_id: Optional[str] = None
    recommended_standard: Optional[str] = None
    reason: str = ""


@dataclass
class SynonymMergeSuggestion:
    keep: str
    merge_as_aliases: List[str] = field(default_factory=list)
    group_id: Optional[str] = None
    reason: str = ""


PendingSuggestFn = Callable[[Dict, Sequence[str]], PendingTagSuggestion]
SynonymAuditFn = Callable[[Sequence[str]], List[SynonymMergeSuggestion]]


def rule_pending_suggestion(
    pending_row: Dict,
    standard_tags: Sequence[str],
) -> PendingTagSuggestion:
    """
    无模型时的规则建议：
    - 若原文是某标准词的子串/超串 → link_alias
    - 否则 → approve_standard（沿用原 group_id）
    """
    raw = str(pending_row.get("raw_text") or "").strip()
    gid = str(pending_row.get("group_id") or "custom").strip()
    pid = int(pending_row.get("id") or 0)
    standards = [str(s).strip() for s in standard_tags if str(s).strip()]

    for std in standards:
        if not std or std == raw:
            continue
        if raw in std or std in raw:
            return PendingTagSuggestion(
                pending_id=pid,
                raw_text=raw,
                group_id=gid,
                action="link_alias",
                recommended_standard=std,
                reason=f"与标准词「{std}」近形/包含，建议作别名",
            )

    return PendingTagSuggestion(
        pending_id=pid,
        raw_text=raw,
        group_id=gid,
        action="approve_standard",
        recommended_group_id=gid,
        reason="库内无近形标准词，建议批准为新标准词",
    )


def rule_synonym_audit(standard_tags: Sequence[str]) -> List[SynonymMergeSuggestion]:
    """
    规则近义巡检：包含关系的词对 → 较短为 keep，较长并入别名。
    """
    tags = sorted({str(t).strip() for t in standard_tags if str(t).strip()}, key=lambda x: (len(x), x))
    used: Set[str] = set()
    suggestions: List[SynonymMergeSuggestion] = []

    for i, a in enumerate(tags):
        if a in used:
            continue
        aliases: List[str] = []
        for b in tags[i + 1 :]:
            if b in used:
                continue
            if a != b and (a in b or b in a):
                # 较短为 keep
                if len(a) <= len(b):
                    keep, merge = a, b
                else:
                    keep, merge = b, a
                if keep == a:
                    aliases.append(merge)
                    used.add(merge)
                else:
                    # b 更短：以 b 为 keep，当前 a 应并入 b
                    # 延后到以 b 为中心处理
                    continue
        if aliases:
            used.add(a)
            suggestions.append(
                SynonymMergeSuggestion(
                    keep=a,
                    merge_as_aliases=aliases,
                    reason="规则：包含/近形关系",
                )
            )
    return suggestions


def apply_synonym_merges_plan(
    suggestions: Sequence[SynonymMergeSuggestion],
) -> Dict[str, str]:
    """
    将确认后的合并建议转为 alias -> standard 映射（纯数据，不写库）。
    """
    alias_to_std: Dict[str, str] = {}
    for s in suggestions:
        keep = (s.keep or "").strip()
        if not keep:
            continue
        for a in s.merge_as_aliases or []:
            al = str(a).strip()
            if al and al != keep:
                alias_to_std[al] = keep
    return alias_to_std

# ---------- 词表冷启动 AI 分簇 ----------

from typing import Any

from core.tag_normalize import DEFAULT_CLOSED_GROUP_IDS, DEFAULT_SUGGESTION_GROUP_ID
from core.tag_vocab import ColdStartCluster, default_rule_cluster, heuristic_assign_group

ALLOWED_COLD_START_GROUPS = frozenset(
    list(DEFAULT_CLOSED_GROUP_IDS) + [DEFAULT_SUGGESTION_GROUP_ID, "custom"]
)

COLD_START_AI_CHUNK = 80

# 产品开关：词表冷启动 AI 分簇效果未达标时暂停；代码保留，改为 True 可恢复
COLD_START_AI_ENABLED = False


def rule_cold_start_cluster(words: Sequence[str]) -> List[ColdStartCluster]:
    """规则分簇 + 启发式归组（AI 失败时的降级路径）。"""
    clusters = default_rule_cluster(words)
    out: List[ColdStartCluster] = []
    for c in clusters:
        gid = heuristic_assign_group(c.standard)
        if gid not in ALLOWED_COLD_START_GROUPS:
            gid = DEFAULT_SUGGESTION_GROUP_ID
        out.append(
            ColdStartCluster(
                standard=c.standard,
                aliases=list(c.aliases or []),
                group_id=gid,
            )
        )
    return out


def parse_ai_cold_start_clusters(
    data: Any,
    allowed_words: Sequence[str],
) -> List[ColdStartCluster]:
    """解析 AI 冷启动分簇 JSON，并硬过滤词表与 group_id。"""
    allowed = {str(w).strip() for w in allowed_words if str(w).strip()}
    if not allowed or not isinstance(data, dict):
        return []

    raw_clusters = data.get("clusters")
    if not isinstance(raw_clusters, list):
        return []

    used: Set[str] = set()
    result: List[ColdStartCluster] = []

    for item in raw_clusters:
        if not isinstance(item, dict):
            continue
        std = str(item.get("standard") or "").strip()
        if not std or std not in allowed or std in used:
            continue
        gid = str(item.get("group_id") or DEFAULT_SUGGESTION_GROUP_ID).strip().lower()
        if gid not in ALLOWED_COLD_START_GROUPS:
            gid = DEFAULT_SUGGESTION_GROUP_ID
        aliases_raw = item.get("aliases") or []
        if not isinstance(aliases_raw, list):
            aliases_raw = []
        aliases: List[str] = []
        for a in aliases_raw:
            al = str(a or "").strip()
            if not al or al == std or al not in allowed or al in used:
                continue
            aliases.append(al)
            used.add(al)
        used.add(std)
        result.append(ColdStartCluster(standard=std, aliases=aliases, group_id=gid))

    for w in sorted(allowed):
        if w not in used:
            result.append(
                ColdStartCluster(
                    standard=w,
                    aliases=[],
                    group_id=heuristic_assign_group(w),
                )
            )
            used.add(w)

    return result


def cold_start_ai_system_prompt() -> str:
    return (
        "你是影视素材标签词表架构师。任务：对用户给出的中文标签草稿做近义分簇，"
        "每簇选定一个标准词，其余为别名，并映射到标签组。"
        "只输出 JSON，不要解释。"
    )


def cold_start_ai_user_prompt(words: Sequence[str]) -> str:
    word_list = "\n".join(f"- {w}" for w in words)
    groups = "mood（氛围）, subject（主体）, location（场景）, action（动作）, custom（建议）"
    example = (
        '{"clusters":[{"standard":"男性","aliases":["男","男人"],"group_id":"subject"}]}'
    )
    return (
        "请对下列标签草稿做近义分簇与归组。\n"
        "硬性约束：\n"
        "1. standard 与 aliases 必须全部来自下列词，不得发明新词；\n"
        "2. 每个词只能出现在一个簇中（要么 standard 要么 alias）；\n"
        f"3. group_id 只能是：{groups}；\n"
        "4. 近义词合并：每簇一个标准词 + 若干别名；\n"
        f"5. 输出 JSON 对象，结构示例：\n{example}\n\n"
        f"草稿词列表：\n{word_list}\n"
    )

