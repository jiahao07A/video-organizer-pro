# -*- coding: utf-8 -*-
"""高级复合筛选：纯函数判定 + QSortFilterProxyModel。"""
import json
from PySide6.QtCore import Qt, QSortFilterProxyModel, QDate

from core.tag_normalize import expand_tags_for_filter


def video_matches_filter(video: dict, filter_params: dict) -> bool:
    """
    判断单条视频是否通过高级筛选（次接缝，可单测）。
    filter_params 键：text, categories, tags, date_start, date_end, only_dup,
    analysis_statuses, tag_match_mode (any|all), summary_empty (any|empty|nonempty),
    alias_map（可选，{别名: 标准词}，用于扩展命中）
    基调通过标签（含氛围标准词）筛选，不再提供独立 emotions 维。
    """
    if not video:
        return False
    params = filter_params or {}

    text = (params.get("text") or "").lower()
    if text:
        content = (
            str(video.get("filename", "")).lower()
            + str(video.get("category", "")).lower()
            + str(video.get("tags", "")).lower()
            + str(video.get("summary", "")).lower()
        )
        if text not in content:
            return False

    cats = params.get("categories") or []
    if cats and video.get("category") not in cats:
        return False

    target_tags = params.get("tags") or []
    if target_tags:
        video_tags = video.get("tags", [])
        if isinstance(video_tags, str):
            try:
                video_tags = json.loads(video_tags)
            except Exception:
                video_tags = []
        vset = set(video_tags or [])
        alias_map = params.get("alias_map") or {}
        tset = expand_tags_for_filter(target_tags, alias_map)
        mode = params.get("tag_match_mode") or "any"
        if mode == "all":
            # all：每个筛选词在扩展后须能命中视频上的某个标签（视频侧亦按标准词）
            for t in target_tags:
                one = expand_tags_for_filter([t], alias_map)
                if not vset.intersection(one):
                    return False
        else:
            if not vset.intersection(tset):
                return False

    statuses = params.get("analysis_statuses") or []
    if statuses:
        st = (video.get("status") or "pending").lower()
        if st in ("", "none", "null"):
            st = "pending"
        wanted = set()
        for s in statuses:
            if s in ("未分析", "pending"):
                wanted.add("pending")
            elif s in ("已分析", "analyzed"):
                wanted.add("analyzed")
            elif s in ("已重命名", "renamed"):
                wanted.add("renamed")
            elif s in ("未命名", "unnamed", "not_renamed"):
                # 尚未执行重命名：未分析 + 已分析（相对「已重命名」）
                wanted.add("pending")
                wanted.add("analyzed")
            else:
                wanted.add(str(s).lower())
        if st not in wanted:
            return False

    summary_mode = params.get("summary_empty") or "any"
    if summary_mode != "any":
        summary = (video.get("summary") or "").strip()
        if summary_mode == "empty" and summary:
            return False
        if summary_mode == "nonempty" and not summary:
            return False

    start_date = params.get("date_start")
    end_date = params.get("date_end")
    if start_date or end_date:
        v_date_str = str(video.get("timestamp", "") or "")[:10]
        if v_date_str:
            v_date = QDate.fromString(v_date_str, "yyyy-MM-dd")
            if not v_date.isValid():
                from PySide6.QtCore import QDateTime
                v_dt = QDateTime.fromString(str(video.get("timestamp", "")), Qt.ISODate)
                if v_dt.isValid():
                    v_date = v_dt.date()
            if v_date.isValid():
                if start_date and v_date < start_date:
                    return False
                if end_date and v_date > end_date:
                    return False

    if params.get("only_dup") and not video.get("is_duplicate", False):
        return False

    return True


class AdvancedSortFilterProxyModel(QSortFilterProxyModel):
    """高级复合筛选代理模型"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.filter_params = {
            "text": "",
            "categories": [],
            "tags": [],
            "date_start": None,
            "date_end": None,
            "only_dup": False,
            "analysis_statuses": [],
            "tag_match_mode": "any",
            "summary_empty": "any",
        }

    def set_filter_params(self, params):
        self.filter_params.update(params or {})
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row, source_parent):
        model = self.sourceModel()
        idx = model.index(source_row, 0, source_parent)
        if not idx.isValid():
            return False
        video = model.videos[source_row]
        return video_matches_filter(video, self.filter_params)