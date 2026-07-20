# -*- coding: utf-8 -*-
import os
import json
from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex, QSize, QThreadPool
from PySide6.QtGui import QPixmap
from ..workers.thumbnail_loader import ThumbnailLoader

class VideoTableModel(QAbstractTableModel):
    """视频数据模型"""
    def __init__(self, videos=None):
        super().__init__()
        self.videos = videos or []
        self.headers = ["选择", "缩略图", "文件名", "分类", "标签", "状态"]
        self.checked_items = set() 
        self.thumbnail_cache = {}
        
        self.thumbnail_pool = QThreadPool()
        self.thumbnail_pool.setMaxThreadCount(4)
        self.loading_paths = set()

    def rowCount(self, parent=QModelIndex()):
        return len(self.videos)

    def columnCount(self, parent=QModelIndex()):
        return len(self.headers)
    
    def on_thumbnail_loaded(self, path, image):
        if path in self.loading_paths:
            self.loading_paths.remove(path)
        
        pixmap = QPixmap.fromImage(image)
        
        # 简单的缓存清理策略
        if len(self.thumbnail_cache) > 200:
             self.thumbnail_cache.pop(next(iter(self.thumbnail_cache)))
             
        self.thumbnail_cache[path] = pixmap
        
        # 刷新相关行
        for row, video in enumerate(self.videos):
             if video.get("thumbnail_path") == path or video.get("thumbnail") == path:
                 tl = self.index(row, 1)
                 br = self.index(row, 2)
                 self.dataChanged.emit(tl, br, [Qt.DecorationRole])

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self.videos)):
            return None

        row_data = self.videos[index.row()]
        col = index.column()

        if role == Qt.DisplayRole:
            if col == 2: return row_data.get("filename", "")
            if col == 3: return row_data.get("category", "")
            if col == 4:
                tags = row_data.get("tags", [])
                if isinstance(tags, str):
                    try: tags = json.loads(tags)
                    except: tags = []
                return ", ".join(tags)
            if col == 5: return row_data.get("status", "")
        
        elif role == Qt.CheckStateRole:
            if col == 0:
                return Qt.Checked if row_data.get("path") in self.checked_items else Qt.Unchecked
        
        elif role == Qt.DecorationRole:
            if col == 1:
                thumb_path = row_data.get("thumbnail_path") or row_data.get("thumbnail")
                if not thumb_path:
                    return None
                
                if thumb_path in self.thumbnail_cache:
                    return self.thumbnail_cache[thumb_path]
                
                # 异步加载
                if thumb_path not in self.loading_paths and os.path.exists(thumb_path):
                    self.loading_paths.add(thumb_path)
                    loader = ThumbnailLoader(thumb_path, QSize(320, 180))
                    loader.signals.loaded.connect(self.on_thumbnail_loaded)
                    self.thumbnail_pool.start(loader)
                
                return None 
        
        elif role == Qt.TextAlignmentRole:
            return Qt.AlignCenter

        return None

    def setData(self, index, value, role=Qt.EditRole):
        if not index.isValid():
            return False
            
        if role == Qt.CheckStateRole and index.column() == 0:
            path = self.videos[index.row()].get("path")
            if value == Qt.Checked or value == True or value == 2:
                self.checked_items.add(path)
            else:
                self.checked_items.discard(path)
            self.dataChanged.emit(index, index, [Qt.CheckStateRole])
            return True
        return False

    def flags(self, index):
        if not index.isValid():
            return Qt.NoItemFlags
        
        flags = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        if index.column() == 0:
            flags |= Qt.ItemIsUserCheckable
        return flags

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return self.headers[section]
        return None

    def update_data(self, new_videos):
        self.beginResetModel()
        self.videos = new_videos
        self.thumbnail_cache.clear()
        self.loading_paths.clear()
        self.endResetModel()
