# -*- coding: utf-8 -*-
from PySide6.QtCore import Qt, QAbstractListModel, QModelIndex, Signal, QSortFilterProxyModel, QMimeData
import json
from typing import List, Dict, Any, Optional

class TagFilterProxyModel(QSortFilterProxyModel):
    def __init__(self, dimension_id, parent=None):
        super().__init__(parent)
        self.dimension_id = dimension_id
        self.filter_text = ""

    def set_filter_text(self, text):
        self.filter_text = text.lower()
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row, source_parent):
        model = self.sourceModel()
        idx = model.index(source_row, 0, source_parent)
        
        # 维度过滤
        dim = model.data(idx, model.DIMENSION_ROLE)
        if self.dimension_id and dim != self.dimension_id:
            return False
            
        # 文本过滤（标准词 + 别名）
        if self.filter_text:
            name = model.data(idx, model.NAME_ROLE) or ""
            aliases = model.data(idx, model.ALIASES_ROLE) or []
            haystack = " ".join([str(name), *[str(a) for a in aliases]]).lower()
            if self.filter_text not in haystack:
                return False
                
        return True

class TagItem:
    def __init__(self, id: int, name: str, dimension: str, usage_count: int = 0, color: Optional[str] = None, parent_id: Optional[int] = None, is_person: bool = False, aliases: Optional[List[str]] = None):
        self.id = id
        self.name = name
        self.dimension = dimension  # 分类 ID: "pool", "C1", "C2", etc.
        self.usage_count = usage_count
        self.color = color
        self.parent_id = parent_id
        self.is_person = is_person
        self.aliases = list(aliases or [])
        self.selected = False

class TagListModel(QAbstractListModel):
    """
    标签列表模型，支持标签的基本属性和选中状态
    """
    ID_ROLE = Qt.UserRole + 1
    NAME_ROLE = Qt.UserRole + 2
    DIMENSION_ROLE = Qt.UserRole + 3
    USAGE_ROLE = Qt.UserRole + 4
    COLOR_ROLE = Qt.UserRole + 5
    SELECTED_ROLE = Qt.UserRole + 6
    PARENT_ID_ROLE = Qt.UserRole + 7
    FULL_PATH_ROLE = Qt.UserRole + 8 # 返回 "父 > 子" 格式的完整路径
    IS_PERSON_ROLE = Qt.UserRole + 9
    ALIASES_ROLE = Qt.UserRole + 10  # 别名列表（标准词管理视图展示用）

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tags: List[TagItem] = []

    def rowCount(self, parent=QModelIndex()):
        return len(self._tags)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self._tags)):
            return None
        
        tag = self._tags[index.row()]
        
        if role == Qt.DisplayRole:
            # 如果有父标签，显示层级路径
            if tag.parent_id:
                return self.get_full_path(tag)
            return tag.name
        elif role == self.NAME_ROLE:
            return tag.name
        elif role == self.ID_ROLE:
            return tag.id
        elif role == self.DIMENSION_ROLE:
            return tag.dimension
        elif role == self.USAGE_ROLE:
            return tag.usage_count
        elif role == self.COLOR_ROLE:
            return tag.color
        elif role == self.SELECTED_ROLE:
            return tag.selected
        elif role == self.PARENT_ID_ROLE:
            return tag.parent_id
        elif role == self.FULL_PATH_ROLE:
            return self.get_full_path(tag)
        elif role == self.IS_PERSON_ROLE:
            return tag.is_person
        elif role == self.ALIASES_ROLE:
            return list(tag.aliases)
        elif role == Qt.ToolTipRole:
            path_str = self.get_full_path(tag)
            tip = f"完整路径: {path_str}\n使用次数: {tag.usage_count}"
            if tag.aliases:
                tip += f"\n别名: {'、'.join(tag.aliases)}"
            return tip
        
        return None

    def get_full_path(self, tag: TagItem) -> str:
        """递归获取标签的完整层级路径"""
        path = [tag.name]
        curr_parent_id = tag.parent_id
        visited = {tag.id} # 简单防循环
        
        while curr_parent_id:
            parent_tag = next((t for t in self._tags if t.id == curr_parent_id), None)
            if parent_tag and parent_tag.id not in visited:
                path.insert(0, parent_tag.name)
                visited.add(parent_tag.id)
                curr_parent_id = parent_tag.parent_id
            else:
                break
        return " > ".join(path)

    def setData(self, index, value, role=Qt.EditRole):
        if not index.isValid() or not (0 <= index.row() < len(self._tags)):
            return False
        
        tag = self._tags[index.row()]
        if role == self.SELECTED_ROLE:
            tag.selected = bool(value)
            self.dataChanged.emit(index, index, [role])
            return True
        elif role == self.DIMENSION_ROLE:
            tag.dimension = value
            self.dataChanged.emit(index, index, [role])
            return True
        elif role == self.PARENT_ID_ROLE:
            tag.parent_id = value
            self.dataChanged.emit(index, index, [role, self.FULL_PATH_ROLE, Qt.DisplayRole])
            return True
        elif role == Qt.EditRole or role == self.NAME_ROLE:
            tag.name = value
            self.dataChanged.emit(index, index, [role, Qt.DisplayRole, self.FULL_PATH_ROLE])
            return True
            
        return False

    def flags(self, index):
        if not index.isValid():
            return Qt.ItemIsDropEnabled
        return Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable | Qt.ItemIsDragEnabled | Qt.ItemIsDropEnabled

    def supportedDropActions(self):
        return Qt.MoveAction | Qt.CopyAction

    def mimeTypes(self):
        return ["application/x-tag-data"]

    def mimeData(self, indexes):
        mime_data = QMimeData()
        tags_info = []
        for index in indexes:
            if index.isValid():
                tag = self._tags[index.row()]
                tags_info.append({
                    "id": tag.id,
                    "name": tag.name,
                    "source_row": index.row(),
                    "source_dim": tag.dimension
                })
        mime_data.setData("application/x-tag-data", json.dumps(tags_info).encode("utf-8"))
        return mime_data

    def dropMimeData(self, data, action, row, column, parent):
        # 实际逻辑移至 View 层处理，以获得更好的跨列支持
        return False

    def move_tags_to_dimension(self, rows: List[int], target_dimension: str):
        """批量移动标签到新维度"""
        for row in rows:
            if 0 <= row < len(self._tags):
                tag = self._tags[row]
                if tag.dimension != target_dimension:
                    tag.dimension = target_dimension
                    idx = self.index(row)
                    self.dataChanged.emit(idx, idx, [self.DIMENSION_ROLE])
        return True

    def set_tags(self, tags_data: List[Dict[str, Any]]):
        """
        更新模型数据
        tags_data: 数据库返回的字典列表
        """
        self.beginResetModel()
        self._tags = []
        for item in tags_data:
            tag = TagItem(
                id=item.get("id"),
                name=item.get("tag_name"),
                dimension=item.get("dimension") or "",
                usage_count=item.get("usage_count", 0),
                color=item.get("color"),
                parent_id=item.get("parent_id"),
                is_person=bool(item.get("is_person", 0)),
                aliases=item.get("aliases") or [],
            )
            self._tags.append(tag)
        self.endResetModel()

    def set_aliases(self, alias_map: Dict[str, List[str]]):
        """按标准词名批量写入别名（标准词管理视图展示别名）。"""
        alias_map = alias_map or {}
        changed = False
        for row, tag in enumerate(self._tags):
            new_aliases = list(alias_map.get(tag.name, []) or [])
            if new_aliases != tag.aliases:
                tag.aliases = new_aliases
                idx = self.index(row)
                self.dataChanged.emit(idx, idx, [self.ALIASES_ROLE, Qt.ToolTipRole])
                changed = True
        return changed

    def add_tag(self, tag_data: Dict[str, Any]):
        row = len(self._tags)
        self.beginInsertRows(QModelIndex(), row, row)
        tag = TagItem(
            id=tag_data.get("id"),
            name=tag_data.get("tag_name"),
            dimension=tag_data.get("dimension") or "",
            usage_count=tag_data.get("usage_count", 0),
            color=tag_data.get("color"),
            parent_id=tag_data.get("parent_id"),
            is_person=bool(tag_data.get("is_person", 0)),
            aliases=tag_data.get("aliases") or [],
        )
        self._tags.append(tag)
        self.endInsertRows()

    def remove_tag(self, row: int):
        if 0 <= row < len(self._tags):
            self.beginRemoveRows(QModelIndex(), row, row)
            self._tags.pop(row)
            self.endRemoveRows()
            return True
        return False

    def merge_tags(self, source_rows: List[int], target_name: str):
        """将多个标签合并为一个"""
        if not source_rows:
            return False
        
        # 按行号降序排列，方便删除
        sorted_rows = sorted(source_rows, reverse=True)
        
        # 保留第一个选中的作为基准（或者是新名字）
        target_row = sorted_rows[-1] # 最小的行号
        tag = self._tags[target_row]
        tag.name = target_name
        
        # 删除其它行
        for row in sorted_rows[:-1]:
            self.remove_tag(row)
            
        # 触发基准行的变更通知
        idx = self.index(target_row)
        self.dataChanged.emit(idx, idx, [self.NAME_ROLE, Qt.DisplayRole])
        return True

    def get_tag(self, row: int) -> Optional[TagItem]:
        if 0 <= row < len(self._tags):
            return self._tags[row]
        return None
