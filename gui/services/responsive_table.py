"""仅在可用区域改变时分配列宽；保留Interactive拖动且不写入用户偏好。"""
from PySide6.QtCore import QObject, QEvent, QTimer
from PySide6.QtWidgets import QSizePolicy
from gui.models.video_table import COL_FILENAME, COL_CATEGORY, COL_TAGS, COL_STATUS


class ResponsiveTableLayout(QObject):
    def __init__(self, view):
        super().__init__(view)
        self.view = view
        self.table = view.table_view
        self._initial_split = False
        self._last_width = None
        self._protected_column = None
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.adjust)
        self.table.viewport().installEventFilter(self)
        view.splitter.installEventFilter(self)
        self.table.horizontalHeader().sectionResized.connect(self._manual_section_resized)
        view.splitter.setChildrenCollapsible(False)
        view.splitter.setHandleWidth(8)
        view.splitter.setStretchFactor(0, 7)
        view.splitter.setStretchFactor(1, 3)
        view.detail_panel.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Resize, QEvent.Show):
            self.timer.start(0)
        return False

    def adjust(self):
        if not self.table.isVisible():
            return
        splitter = self.view.splitter
        if not self._initial_split and splitter.width() > 100:
            self._initial_split = True
            width = splitter.width() - splitter.handleWidth()
            splitter.setSizes([int(width * .7), int(width * .3)])
        width = self.table.viewport().width()
        if width <= 0 or width == self._last_width:
            return
        self._last_width = width
        header = self.table.horizontalHeader()
        delta = width - header.length()
        minima = {COL_FILENAME: 140, COL_CATEGORY: 85, COL_TAGS: 110, COL_STATUS: 130}
        columns = [c for c in minima if not self.table.isColumnHidden(c)]
        if not columns or not delta:
            return
        adjustable = [c for c in columns if c != self._protected_column]
        if adjustable:
            columns = adjustable
        self.view._applying_column_widths = True
        try:
            if delta > 0:
                # 优先把多余空间给文件名及标签，不留下右侧空白。
                weights = {COL_FILENAME: 4, COL_CATEGORY: 1, COL_TAGS: 3, COL_STATUS: 2}
                total = sum(weights[c] for c in columns)
                remaining = delta
                for i, column in enumerate(columns):
                    extra = remaining if i == len(columns)-1 else delta * weights[column] // total
                    header.resizeSection(column, header.sectionSize(column) + extra)
                    remaining -= extra
            else:
                remaining = -delta
                while remaining and columns:
                    share = max(1, remaining // len(columns))
                    for column in list(columns):
                        room = max(0, header.sectionSize(column) - minima[column])
                        reduction = min(remaining, share, room)
                        header.resizeSection(column, header.sectionSize(column) - reduction)
                        remaining -= reduction
                        if room <= reduction:
                            columns.remove(column)
                        if not remaining:
                            break
        finally:
            self.view._applying_column_widths = False

    def _manual_section_resized(self, _logical, _old, new):
        if self.view._applying_column_widths or new <= 0:
            return
        # A direct header drag changes the section sum without changing the
        # viewport. Rebalance the remaining columns after Qt finishes the drag.
        self._protected_column = _logical
        self._last_width = None
        self.timer.start(0)
