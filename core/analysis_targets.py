# -*- coding: utf-8 -*-
"""分析目标集：按状态决定是否纳入分析队列（纯逻辑，可单测）。"""
from __future__ import annotations

import os
from typing import Dict, Iterable, List, Optional, Sequence

# 视为「已分析」、默认跳过的状态
ANALYZED_STATUSES = frozenset(
    {
        "analyzed",
        "edited",
        "xmp",
        "renamed",
        "已分析",
        "已完成",
    }
)

# 失败态：默认再次进入目标集
FAILED_STATUSES = frozenset(
    {
        "failed",
        "error",
        "分析失败",
        "fail",
    }
)


def path_status_key(path: str) -> str:
    """统一路径键：绝对路径 + 系统大小写规则，避免 Windows 路径形态不一致漏匹配。"""
    if not path:
        return ""
    try:
        return os.path.normcase(os.path.abspath(path))
    except Exception:
        return str(path).replace("\\", "/").lower()


def normalize_analysis_status(status: Optional[str]) -> str:
    s = (status or "pending").strip().lower()
    if not s:
        return "pending"
    return s


def is_analyzed_status(status: Optional[str]) -> bool:
    raw = (status or "").strip()
    low = raw.lower()
    if low in ANALYZED_STATUSES or raw in ANALYZED_STATUSES:
        return True
    return False


def is_failed_status(status: Optional[str]) -> bool:
    raw = (status or "").strip()
    low = raw.lower()
    return low in FAILED_STATUSES or raw in FAILED_STATUSES


def needs_analysis(status: Optional[str], *, force: bool = False) -> bool:
    """
    是否应纳入分析目标集。
    - force=True：一律纳入（强制重新分析）
    - 已分析：默认排除
    - pending / 失败 / 未知：纳入
    """
    if force:
        return True
    if is_analyzed_status(status):
        return False
    return True


def resolve_analysis_target_paths(
    candidate_paths: Sequence[str],
    path_to_status: Dict[str, str],
    *,
    force: bool = False,
) -> List[str]:
    """
    从候选路径解析分析目标集。
    path_to_status：路径 -> 状态（键建议已用 path_status_key 规范化）；库中无记录时视为 pending。
    禁止：仅因「路径已在库」而排除（在库但 pending 必须纳入）。
    """
    # 预建规范化查找表（兼容调用方传入原始路径键）
    lookup: Dict[str, str] = {}
    for k, v in (path_to_status or {}).items():
        if k is None:
            continue
        lookup[path_status_key(k)] = v
        lookup[k] = v

    out: List[str] = []
    seen = set()
    for p in candidate_paths or []:
        if not p:
            continue
        key = path_status_key(p)
        if key in seen or p in seen:
            continue
        status = lookup.get(key)
        if status is None:
            status = lookup.get(p)
        if status is None:
            status = "pending"
        if needs_analysis(status, force=force):
            out.append(p)
            seen.add(key)
            seen.add(p)
    return out


def build_path_status_map(videos: Iterable[Dict]) -> Dict[str, str]:
    """构建 path_status_key -> status 映射。"""
    m: Dict[str, str] = {}
    for v in videos or []:
        path = v.get("path")
        if path:
            m[path_status_key(path)] = (v.get("status") or "pending")
            m[path] = (v.get("status") or "pending")
    return m