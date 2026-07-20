# -*- coding: utf-8 -*-
import json
from PySide6.QtCore import Qt, QSortFilterProxyModel, QDate

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
            "only_dup": False
        }

    def set_filter_params(self, params):
        self.filter_params.update(params)
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row, source_parent):
        model = self.sourceModel()
        idx = model.index(source_row, 0, source_parent)
        if not idx.isValid(): return False
        
        video = model.videos[source_row]
        
        # 1. 文本搜索
        text = self.filter_params.get("text", "").lower()
        if text:
            content = (
                str(video.get("filename", "")).lower() +
                str(video.get("category", "")).lower() +
                str(video.get("tags", "")).lower() +
                str(video.get("summary", "")).lower() +
                str(video.get("emotion", "")).lower() +
                str(video.get("composition", "")).lower()
            )
            if text not in content:
                return False

        # 2. 分类筛选
        cats = self.filter_params.get("categories", [])
        if cats:
            if video.get("category") not in cats:
                return False

        # 3. 标签筛选 (OR逻辑)
        target_tags = self.filter_params.get("tags", [])
        if target_tags:
            video_tags = video.get("tags", [])
            if isinstance(video_tags, str):
                try: video_tags = json.loads(video_tags)
                except: video_tags = []
            
            if not set(video_tags).intersection(set(target_tags)):
                return False

        # 4. 日期筛选
        start_date = self.filter_params.get("date_start")
        end_date = self.filter_params.get("date_end")
        if start_date or end_date:
            # 假设视频数据中有 timestamp 或 created_at
            v_date_str = video.get("timestamp", "")[:10]
            if v_date_str:
                v_date = QDate.fromString(v_date_str, "yyyy-MM-dd")
                if not v_date.isValid():
                    # 尝试 ISO 格式
                    from PySide6.QtCore import QDateTime
                    v_dt = QDateTime.fromString(video.get("timestamp", ""), Qt.ISODate)
                    if v_dt.isValid():
                        v_date = v_dt.date()

                if v_date.isValid():
                    if start_date and v_date < start_date:
                        return False
                    if end_date and v_date > end_date:
                        return False

        # 5. 重复项筛选
        if self.filter_params.get("only_dup"):
            if not video.get("is_duplicate", False):
                return False

        return True
