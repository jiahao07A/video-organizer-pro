# -*- coding: utf-8 -*-
import os
import json
from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex, QSize, QThreadPool, QSortFilterProxyModel
from PySide6.QtGui import QPixmap
from ..workers.thumbnail_loader import ThumbnailLoader

# 列索引常量（无「选择」勾选列；批量操作走列表选中）
COL_LIST_NO = 0
COL_LIBRARY_ID = 1
COL_THUMB = 2
COL_FILENAME = 3
COL_CATEGORY = 4
COL_TAGS = 5
COL_STATUS = 6

# 旧 8 列方案中的「选择」列索引（仅用于偏好迁移，勿作数据列）
LEGACY_COL_CHECK = 0
LEGACY_COLUMN_COUNT = 8


class VideoTableModel(QAbstractTableModel):
    """视频数据模型：列表序号 / 入库编号 / 缩略图 / 文件名 / 分类 / 标签 / 状态"""

    def __init__(self, videos=None):
        super().__init__()
        self.videos = videos or []
        self.headers = ["#", "入库编号", "缩略图", "文件名", "分类", "标签", "状态"]
        self.thumbnail_cache = {}
        self.thumbnail_pool = QThreadPool()
        self.thumbnail_pool.setMaxThreadCount(4)
        self.loading_paths = set()
        # 代理模型可见行 -> 列表序号（由视图在刷新时可选设置；默认用源行+1）
        self._list_no_by_source_row = {}
        # 分析任务行临时态 path -> 文案（不写库）
        self.temp_status_by_path = {}

    def set_temp_status_map(self, mapping: dict):
        """更新分析行临时态并刷新状态列（映射未变则不 emit，减轻大批量重绘）。"""
        new_map = dict(mapping or {})
        if new_map == self.temp_status_by_path:
            return
        old = self.temp_status_by_path
        self.temp_status_by_path = new_map
        n = self.rowCount()
        if n <= 0:
            return
        # 小变更时只刷新受影响行；大变更/清空时整列刷新
        changed_paths = set(old.keys()) ^ set(new_map.keys())
        for p in set(old.keys()) & set(new_map.keys()):
            if old.get(p) != new_map.get(p):
                changed_paths.add(p)
        if len(changed_paths) > 40 or not changed_paths:
            tl = self.index(0, COL_STATUS)
            br = self.index(n - 1, COL_STATUS)
            self.dataChanged.emit(tl, br, [Qt.DisplayRole])
            return
        # 按 path 找行
        path_to_row = {}
        for i, v in enumerate(self.videos):
            p = v.get("path") or ""
            if p:
                path_to_row[p] = i
                try:
                    from core.video_organizer_service import normalize_work_path
                    path_to_row[normalize_work_path(p)] = i
                except Exception:
                    pass
        rows = set()
        for p in changed_paths:
            if p in path_to_row:
                rows.add(path_to_row[p])
            # 兼容大小写/斜杠差异：遍历匹配成本高，退化整列
            if len(rows) == 0 and len(changed_paths) <= 8:
                for i, v in enumerate(self.videos):
                    vp = v.get("path") or ""
                    if vp == p or vp.replace("\\", "/").lower() == str(p).replace("\\", "/").lower():
                        rows.add(i)
        if not rows:
            tl = self.index(0, COL_STATUS)
            br = self.index(n - 1, COL_STATUS)
            self.dataChanged.emit(tl, br, [Qt.DisplayRole])
            return
        for r in rows:
            idx = self.index(r, COL_STATUS)
            self.dataChanged.emit(idx, idx, [Qt.DisplayRole])

    def clear_temp_status_map(self):
        self.set_temp_status_map({})

    def rowCount(self, parent=QModelIndex()):
        return len(self.videos)

    def columnCount(self, parent=QModelIndex()):
        return len(self.headers)

    def set_list_numbers(self, source_row_to_no: dict):
        """由视图根据当前筛选/排序后的可见顺序写入列表序号。

        注意：仅更新内存映射；若映射未变则不 emit，避免
        dataChanged → 代理重排/重滤 → 再次刷新 的递归。
        """
        new_map = dict(source_row_to_no or {})
        if new_map == self._list_no_by_source_row:
            return
        self._list_no_by_source_row = new_map
        if not self.videos:
            return
        tl = self.index(0, COL_LIST_NO)
        br = self.index(len(self.videos) - 1, COL_LIST_NO)
        # 只声明 DisplayRole，且调用方应关闭 proxy dynamicSortFilter 再 emit
        self.dataChanged.emit(tl, br, [Qt.DisplayRole])

    def on_thumbnail_loaded(self, path, image):
        if path in self.loading_paths:
            self.loading_paths.remove(path)

        pixmap = QPixmap.fromImage(image)

        if len(self.thumbnail_cache) > 200:
            self.thumbnail_cache.pop(next(iter(self.thumbnail_cache)))

        self.thumbnail_cache[path] = pixmap

        for row, video in enumerate(self.videos):
            if video.get("thumbnail_path") == path or video.get("thumbnail") == path:
                tl = self.index(row, COL_THUMB)
                br = self.index(row, COL_THUMB)
                self.dataChanged.emit(tl, br, [Qt.DecorationRole])

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self.videos)):
            return None

        row_data = self.videos[index.row()]
        col = index.column()
        row = index.row()

        if role == Qt.DisplayRole:
            if col == COL_LIST_NO:
                return self._list_no_by_source_row.get(row, row + 1)
            if col == COL_LIBRARY_ID:
                lid = row_data.get("library_id")
                if lid is None:
                    lid = row_data.get("id")
                return lid if lid is not None else ""
            if col == COL_FILENAME:
                return row_data.get("filename", "")
            if col == COL_CATEGORY:
                return row_data.get("category", "") or ""
            if col == COL_TAGS:
                tags = row_data.get("tags", [])
                if isinstance(tags, str):
                    try:
                        tags = json.loads(tags)
                    except Exception:
                        tags = []
                return ", ".join(tags)
            if col == COL_STATUS:
                path = row_data.get("path") or ""
                temp = getattr(self, "temp_status_by_path", None) or {}
                if path and path in temp:
                    return temp[path]
                # 尝试规范化键
                try:
                    from core.analysis_targets import path_status_key
                    pk = path_status_key(path)
                    if pk in temp:
                        return temp[pk]
                except Exception:
                    pass
                return row_data.get("status", "")

        elif role == Qt.UserRole:
            # 卡片与其它代理可取整行数据
            return row_data

        elif role == Qt.DecorationRole:
            if col == COL_THUMB:
                thumb_path = row_data.get("thumbnail_path") or row_data.get("thumbnail")
                if not thumb_path:
                    return None

                if thumb_path in self.thumbnail_cache:
                    return self.thumbnail_cache[thumb_path]

                if thumb_path not in self.loading_paths and os.path.exists(thumb_path):
                    self.loading_paths.add(thumb_path)
                    loader = ThumbnailLoader(thumb_path, QSize(320, 180))
                    loader.signals.loaded.connect(self.on_thumbnail_loaded)
                    self.thumbnail_pool.start(loader)

                return None

        elif role == Qt.TextAlignmentRole:
            return Qt.AlignCenter

        return None

    def flags(self, index):
        if not index.isValid():
            return Qt.NoItemFlags
        return Qt.ItemIsEnabled | Qt.ItemIsSelectable

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return self.headers[section]
        return None

    def update_data(self, new_videos):
        self.beginResetModel()
        self.videos = new_videos
        self.thumbnail_cache.clear()
        self.loading_paths.clear()
        self._list_no_by_source_row = {}
        self.endResetModel()


def migrate_table_column_prefs(prefs: dict, current_column_count: int = 7) -> dict:
    """旧 8 列（含选择列 0）偏好 → 新 7 列：丢弃选择列，其余索引 -1；越界键忽略。

    识别旧方案：存在列键 7（8 列最大下标），或显式 legacy 标记。
    新方案写入时应带 schema=no_check_col。
    """
    if not prefs:
        return {}
    schema = prefs.get("_schema") or prefs.get("schema")
    if schema in ("no_check_col", "v2", 2, "2"):
        out = {}
        for k, v in prefs.items():
            if str(k).startswith("_") or k in ("schema",):
                continue
            try:
                i = int(k)
            except (TypeError, ValueError):
                continue
            if 0 <= i < current_column_count:
                out[str(i)] = v
        return out

    int_entries = []
    for k, v in prefs.items():
        if str(k).startswith("_") or k in ("schema",):
            continue
        try:
            int_entries.append((int(k), v))
        except (TypeError, ValueError):
            continue
    if not int_entries:
        return {}
    max_key = max(i for i, _ in int_entries)
    key_set = {i for i, _ in int_entries}
    # 旧 8 列：有下标 7，或 max>=7
    is_legacy_8 = max_key >= LEGACY_COLUMN_COUNT - 1 or 7 in key_set
    if is_legacy_8:
        shifted = {}
        for i, v in int_entries:
            if i == LEGACY_COL_CHECK:
                continue
            ni = i - 1
            if 0 <= ni < current_column_count:
                shifted[str(ni)] = v
        return shifted
    out = {}
    for i, v in int_entries:
        if 0 <= i < current_column_count:
            out[str(i)] = v
    return out