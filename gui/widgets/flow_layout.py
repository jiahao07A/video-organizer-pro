# -*- coding: utf-8 -*-
from PySide6.QtWidgets import QLayout
from PySide6.QtCore import Qt, QSize, QPoint, QRect

class FlowLayout(QLayout):
    """按可用宽度换行的标签和工具栏布局。"""
    def __init__(self, parent=None, margin=0, spacing=-1):
        super().__init__(parent)
        self.setContentsMargins(margin, margin, margin, margin)
        self.setSpacing(spacing)
        self.items = []

    def __del__(self):
        item = self.takeAt(0)
        while item:
            item = self.takeAt(0)

    def addItem(self, item):
        self.items.append(item)

    def count(self):
        return len(self.items)

    def itemAt(self, index):
        if 0 <= index < len(self.items):
            return self.items[index]
        return None

    def takeAt(self, index):
        if 0 <= index < len(self.items):
            return self.items.pop(index)
        return None

    def expandingDirections(self):
        return Qt.Orientations(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        height = self._do_layout(QRect(0, 0, width, 0), True)
        return height

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self.items:
            if item.isEmpty():
                continue
            size = size.expandedTo(item.minimumSize())
        margin = self.contentsMargins()
        size += QSize(margin.left() + margin.right(), margin.top() + margin.bottom())
        return size

    def _do_layout(self, rect, test_only):
        margin = self.contentsMargins()
        area = rect.adjusted(margin.left(), margin.top(), -margin.right(), -margin.bottom())
        x = area.x()
        y = area.y()
        line_height = 0
        spacing = max(0, self.spacing())

        for item in self.items:
            if item.isEmpty():
                continue
            wid = item.widget()
            if wid:
                hint = item.sizeHint()
                width = max(item.minimumSize().width(), min(hint.width(), max(0, area.width())))
                size = QSize(width, hint.height())
                next_x = x + size.width() + spacing
                if next_x - spacing > area.right() + 1 and line_height > 0:
                    x = area.x()
                    y = y + line_height + spacing
                    next_x = x + size.width() + spacing
                    line_height = 0

                if not test_only:
                    item.setGeometry(QRect(QPoint(x, y), size))

                x = next_x
                line_height = max(line_height, size.height())

        return y + line_height - rect.y() + margin.bottom()
