# -*- coding: utf-8 -*-
from PySide6.QtWidgets import QComboBox
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QStandardItemModel, QStandardItem

class CheckableComboBox(QComboBox):
    """支持多选的下拉框"""
    itemsChanged = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.view().pressed.connect(self.handle_item_pressed)
        self.setModel(QStandardItemModel(self))
        self.checked_items = []

    def handle_item_pressed(self, index):
        item = self.model().itemFromIndex(index)
        if item.checkState() == Qt.Checked:
            item.setCheckState(Qt.Unchecked)
            if item.text() in self.checked_items:
                self.checked_items.remove(item.text())
        else:
            item.setCheckState(Qt.Checked)
            if item.text() not in self.checked_items:
                self.checked_items.append(item.text())
        self.itemsChanged.emit(self.checked_items)

    def add_item(self, text, data=None):
        item = QStandardItem(text)
        item.setCheckable(True)
        item.setCheckState(Qt.Unchecked)
        if data: item.setData(data)
        self.model().appendRow(item)
    
    def clear_items(self):
        self.model().clear()
        self.checked_items = []
        self.clear() 

    def get_checked_items(self):
        return self.checked_items
