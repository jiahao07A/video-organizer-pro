# -*- coding: utf-8 -*-
"""标签库 AI 辅助：待审建议 / 近义巡检 / 标准词助手（可注入假实现；确认前不写库）。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Set


@dataclass
class PendingTagSuggestion:
    pending_id: int
    raw_text: str
    group_id: str
    action: str  # approve_standard | link_alias | discard
    recommended_group_id: Optional[str] = None
    recommended_standard: Optional[str] = None
    reason: str = ""
    source: str = "rule"  # model | rule


@dataclass
class SynonymMergeSuggestion:
    keep: str
    merge_as_aliases: List[str] = field(default_factory=list)
    group_id: Optional[str] = None
    reason: str = ""
    source: str = "rule"


@dataclass
class StandardTagAssistSuggestion:
    tag_name: str
    current_group_id: str
    suggested_aliases: List[str] = field(default_factory=list)
    recommended_group_id: Optional[str] = None
    reason: str = ""
    source: str = "rule"


PendingSuggestFn = Callable[[Dict, Sequence[str]], PendingTagSuggestion]
SynonymAuditFn = Callable[[Sequence[str]], List[SynonymMergeSuggestion]]
StandardAssistFn = Callable[[str, str, Sequence[str], Sequence[str]], StandardTagAssistSuggestion]


ALLOWED_PENDING_ACTIONS = frozenset({"approve_standard", "link_alias", "discard"})
ALLOWED_GROUP_IDS = frozenset({"mood", "subject", "location", "action", "custom"})

DEFAULT_TAG_AI_PROMPTS = {
    "pending_tag_ai": (
        "你是影视素材标签库审稿员。根据待审词与标准词表，给出处理建议。"
        "只输出 JSON：action 为 approve_standard|link_alias|discard；"
        "approve 时给 recommended_group_id；link_alias 时给 recommended_standard；可附 reason。"
    ),
    "synonym_audit": (
        "你是词表近义审稿员。找出应合并为「标准词+别名」的近义组。"
        "只输出 JSON：{\"merges\":[{\"keep\":\"保留词\",\"merge_as_aliases\":[\"并入1\"],\"reason\":\"...\"}]}"
        "keep 与 aliases 必须来自给定标准词列表，不得发明新词。"
    ),
    "standard_tag_ai": (
        "你是标签库运营助手。针对一个标准词，建议可挂别名与是否改组。"
        "只输出 JSON：{\"aliases\":[\"...\"],\"recommended_group_id\":\"mood|subject|location|action|custom|null\","
        "\"reason\":\"...\"}。aliases 不要包含标准词本身；不要建议并入其他标准词。"
    ),
}


def rule_pending_suggestion(
    pending_row: Dict,
    standard_tags: Sequence[str],
) -> PendingTagSuggestion:
    """无模型时的规则建议。"""
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
                source="rule",
            )

    return PendingTagSuggestion(
        pending_id=pid,
        raw_text=raw,
        group_id=gid,
        action="approve_standard",
        recommended_group_id=gid if gid in ALLOWED_GROUP_IDS else "custom",
        reason="库内无近形标准词，建议批准为新标准词",
        source="rule",
    )


def parse_pending_ai_response(
    data: Any,
    pending_row: Dict,
    standard_tags: Sequence[str],
) -> Optional[PendingTagSuggestion]:
    if not isinstance(data, dict):
        return None
    raw = str(pending_row.get("raw_text") or "").strip()
    gid = str(pending_row.get("group_id") or "custom").strip()
    pid = int(pending_row.get("id") or 0)
    action = str(data.get("action") or "").strip().lower()
    if action not in ALLOWED_PENDING_ACTIONS:
        return None
    reason = str(data.get("reason") or "").strip()
    standards = {str(s).strip() for s in standard_tags if str(s).strip()}

    if action == "discard":
        return PendingTagSuggestion(
            pending_id=pid,
            raw_text=raw,
            group_id=gid,
            action="discard",
            reason=reason or "模型建议丢弃",
            source="model",
        )
    if action == "link_alias":
        std = str(data.get("recommended_standard") or "").strip()
        if not std or std not in standards:
            return None
        return PendingTagSuggestion(
            pending_id=pid,
            raw_text=raw,
            group_id=gid,
            action="link_alias",
            recommended_standard=std,
            reason=reason or f"模型建议挂为「{std}」别名",
            source="model",
        )
    rg = str(data.get("recommended_group_id") or gid or "custom").strip().lower()
    if rg not in ALLOWED_GROUP_IDS:
        rg = "custom"
    return PendingTagSuggestion(
        pending_id=pid,
        raw_text=raw,
        group_id=gid,
        action="approve_standard",
        recommended_group_id=rg,
        reason=reason or "模型建议批准为新标准词",
        source="model",
    )


def pending_ai_user_prompt(pending_row: Dict, standard_tags: Sequence[str]) -> str:
    raw = str(pending_row.get("raw_text") or "").strip()
    gid = str(pending_row.get("group_id") or "").strip()
    standards = [str(s).strip() for s in standard_tags if str(s).strip()][:80]
    std_list = "、".join(standards) if standards else "（空）"
    return (
        f"待审词：{raw}\n来源组提示：{gid or '无'}\n"
        f"当前标准词（节选）：{std_list}\n"
        "请给出 JSON 建议。"
    )


def rule_synonym_audit(standard_tags: Sequence[str]) -> List[SynonymMergeSuggestion]:
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
                if len(a) <= len(b):
                    keep, merge = a, b
                else:
                    keep, merge = b, a
                if keep == a:
                    aliases.append(merge)
                    used.add(merge)
                else:
                    continue
        if aliases:
            used.add(a)
            suggestions.append(
                SynonymMergeSuggestion(
                    keep=a,
                    merge_as_aliases=aliases,
                    reason="规则：包含/近形关系",
                    source="rule",
                )
            )
    return suggestions


def parse_synonym_ai_response(
    data: Any,
    standard_tags: Sequence[str],
) -> List[SynonymMergeSuggestion]:
    allowed = {str(t).strip() for t in standard_tags if str(t).strip()}
    if not allowed or not isinstance(data, dict):
        return []
    merges = data.get("merges")
    if not isinstance(merges, list):
        return []
    out: List[SynonymMergeSuggestion] = []
    used: Set[str] = set()
    for item in merges:
        if not isinstance(item, dict):
            continue
        keep = str(item.get("keep") or "").strip()
        if not keep or keep not in allowed or keep in used:
            continue
        aliases_raw = item.get("merge_as_aliases") or item.get("aliases") or []
        if not isinstance(aliases_raw, list):
            continue
        aliases: List[str] = []
        for a in aliases_raw:
            al = str(a or "").strip()
            if not al or al == keep or al not in allowed or al in used:
                continue
            aliases.append(al)
            used.add(al)
        if not aliases:
            continue
        used.add(keep)
        out.append(
            SynonymMergeSuggestion(
                keep=keep,
                merge_as_aliases=aliases,
                reason=str(item.get("reason") or "模型近义建议").strip(),
                source="model",
            )
        )
    return out


def synonym_ai_user_prompt(standard_tags: Sequence[str]) -> str:
    tags = sorted({str(t).strip() for t in standard_tags if str(t).strip()})
    body = "\n".join(f"- {t}" for t in tags[:120])
    return f"标准词列表：\n{body}\n请输出 merges JSON。"


def apply_synonym_merges_plan(
    suggestions: Sequence[SynonymMergeSuggestion],
) -> Dict[str, str]:
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


def rule_standard_tag_assist(
    tag_name: str,
    current_group_id: str,
    standard_tags: Sequence[str],
    existing_aliases: Sequence[str] = (),
) -> StandardTagAssistSuggestion:
    """
    规则降级：不把其它标准词当别名候选（避免双身份脏数据）。
    合并走近义巡检；此处只提示人工填写。
    """
    name = str(tag_name or "").strip()
    gid = str(current_group_id or "custom").strip()
    _ = standard_tags, existing_aliases  # 保留签名供调用方注入上下文
    return StandardTagAssistSuggestion(
        tag_name=name,
        current_group_id=gid,
        suggested_aliases=[],
        recommended_group_id=None,
        reason="规则降级：未调用模型；请人工填写别名（勿填其它标准词）或改组。合并请走近义巡检。",
        source="rule",
    )


def parse_standard_tag_ai_response(
    data: Any,
    tag_name: str,
    current_group_id: str,
    standard_tags: Sequence[str] = (),
) -> Optional[StandardTagAssistSuggestion]:
    if not isinstance(data, dict):
        return None
    name = str(tag_name or "").strip()
    gid = str(current_group_id or "custom").strip()
    standards = {str(s).strip() for s in standard_tags if str(s).strip()}
    aliases_raw = data.get("aliases") or data.get("suggested_aliases") or []
    if not isinstance(aliases_raw, list):
        aliases_raw = []
    aliases: List[str] = []
    for a in aliases_raw:
        al = str(a or "").strip()
        # 禁止把其它标准词挂成别名（合并走近义巡检）
        if not al or al == name or al in aliases or al in standards:
            continue
        aliases.append(al)
    rg: Optional[str] = None
    rg_raw = data.get("recommended_group_id")
    if rg_raw is not None and str(rg_raw).strip() and str(rg_raw).strip().lower() not in ("null", "none", ""):
        rg = str(rg_raw).strip().lower()
        if rg not in ALLOWED_GROUP_IDS:
            rg = None
        if rg == gid:
            rg = None
    # 空结果视为解析失败，便于上层降级规则
    if not aliases and rg is None and not str(data.get("reason") or "").strip():
        return None
    return StandardTagAssistSuggestion(
        tag_name=name,
        current_group_id=gid,
        suggested_aliases=aliases[:20],
        recommended_group_id=rg,
        reason=str(data.get("reason") or "").strip() or "模型建议",
        source="model",
    )


def standard_tag_ai_user_prompt(
    tag_name: str,
    current_group_id: str,
    existing_aliases: Sequence[str] = (),
) -> str:
    aliases = "、".join(str(a) for a in existing_aliases if str(a).strip()) or "（无）"
    return (
        f"标准词：{tag_name}\n当前组：{current_group_id}\n已有别名：{aliases}\n"
        "请建议更多别名与是否改组（JSON）。"
    )


GROUP_ACCENT_COLORS = {
    "mood": "#E91E63",
    "subject": "#2196F3",
    "location": "#4CAF50",
    "action": "#FF9800",
    "custom": "#757575",
}


def chip_style_tokens(
    group_id: Optional[str] = None,
    accent: Optional[str] = None,
    *,
    theme: str = "dark",
    misspelled: bool = False,
) -> Dict[str, str]:
    """素材标签芯片：浅底 + 组色条 + 主题正文色（纯数据可测）。"""
    gid = str(group_id or "custom").strip().lower() or "custom"
    bar = (accent or GROUP_ACCENT_COLORS.get(gid) or GROUP_ACCENT_COLORS["custom"]).strip()
    if theme == "light":
        bg, text, muted, hover_bg = "#f5f5f5", "#1a1a1a", "#666666", "#eeeeee"
    else:
        bg, text, muted, hover_bg = "#3a3a3a", "#e8e8e8", "#aaaaaa", "#454545"
    border = "#ff4d4f" if misspelled else bar
    return {
        "background": bg,
        "text": text,
        "accent_bar": bar,
        "border": border,
        "muted": muted,
        "hover_background": hover_bg,
        "group_id": gid,
    }


# ---------- 词表冷启动 AI 分簇 ----------

from core.tag_normalize import DEFAULT_CLOSED_GROUP_IDS, DEFAULT_SUGGESTION_GROUP_ID
from core.tag_vocab import ColdStartCluster, default_rule_cluster, heuristic_assign_group

ALLOWED_COLD_START_GROUPS = frozenset(
    list(DEFAULT_CLOSED_GROUP_IDS) + [DEFAULT_SUGGESTION_GROUP_ID, "custom"]
)

COLD_START_AI_CHUNK = 80
COLD_START_AI_ENABLED = False


def rule_cold_start_cluster(words: Sequence[str]) -> List[ColdStartCluster]:
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