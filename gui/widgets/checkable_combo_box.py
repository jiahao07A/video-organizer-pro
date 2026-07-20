# -*- coding: utf-8 -*-
"""支持多选且点选后不自动折叠的下拉框。"""
from PySide6.QtWidgets import QComboBox, QStyledItemDelegate
from PySide6.QtCore import Qt, Signal, QEvent
from PySide6.QtGui import QStandardItemModel, QStandardItem


class CheckableComboBox(QComboBox):
    """多选下拉：勾选/取消时保持弹出，便于连续多选。"""
    itemsChanged = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setModel(QStandardItemModel(self))
        self.setItemDelegate(QStyledItemDelegate(self))
        self.checked_items = []
        self._placeholder = ""
        # 拦截列表视口鼠标事件，避免 QComboBox 默认“点一项就关”
        self.view().viewport().installEventFilter(self)
        self.setEditable(True)
        self.lineEdit().setReadOnly(True)
        self.lineEdit().setPlaceholderText("")
        # 禁止编辑框把焦点抢走导致异常关闭
        self.lineEdit().installEventFilter(self)
        # 防止选中行变化把显示文本改成单项名称
        self.currentIndexChanged.connect(lambda *_: self._refresh_display_text())

    def setPlaceholderText(self, text: str):
        self._placeholder = text or ""
        self._refresh_display_text()

    def showPopup(self):
        super().showPopup()
        self._refresh_display_text()

    def eventFilter(self, obj, event):
        # 列表项：按下/释放都吞掉，仅在释放时切换勾选，弹出保持打开
        if obj is self.view().viewport():
            if event.type() == QEvent.Type.MouseButtonPress:
                return True
            if event.type() == QEvent.Type.MouseButtonRelease:
                index = self.view().indexAt(event.position().toPoint() if hasattr(event, "position") else event.pos())
                if index.isValid():
                    self._toggle_at(index)
                return True
        # 编辑框点击：打开/切换弹出，不进入文本编辑
        if obj is self.lineEdit() and event.type() == QEvent.Type.MouseButtonRelease:
            if self.view().isVisible():
                self.hidePopup()
            else:
                self.showPopup()
            return True
        return super().eventFilter(obj, event)

    def hidePopup(self):
        # 仅允许显式关闭（点外部、再点编辑框、Esc 等走 Qt 默认路径）
        super().hidePopup()
        self._refresh_display_text()

    def _toggle_at(self, index):
        item = self.model().itemFromIndex(index)
        if item is None:
            return
        if item.checkState() == Qt.Checked:
            item.setCheckState(Qt.Unchecked)
            if item.text() in self.checked_items:
                self.checked_items.remove(item.text())
        else:
            item.setCheckState(Qt.Checked)
            if item.text() not in self.checked_items:
                self.checked_items.append(item.text())
        self._refresh_display_text()
        self.itemsChanged.emit(list(self.checked_items))

    def _refresh_display_text(self):
        le = self.lineEdit()
        if not le:
            return
        if not self.checked_items:
            le.setText("")
            le.setPlaceholderText(self._placeholder)
        elif len(self.checked_items) == 1:
            le.setText(self.checked_items[0])
        else:
            le.setText(f"已选 {len(self.checked_items)} 项")

    def add_item(self, text, data=None):
        item = QStandardItem(text)
        item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
        item.setData(Qt.Unchecked, Qt.CheckStateRole)
        if data is not None:
            item.setData(data)
        self.model().appendRow(item)

    def clear_items(self):
        self.model().clear()
        self.checked_items = []
        self._refresh_display_text()

    def get_checked_items(self):
        return list(self.checked_items)