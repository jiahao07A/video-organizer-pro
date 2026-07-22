# -*- coding: utf-8 -*-
from PySide6.QtWidgets import QStyledItemDelegate, QStyle
from PySide6.QtCore import Qt, QSize, QRect, QRectF, QEvent
from PySide6.QtGui import QColor, QPainter, QPainterPath, QBrush, QPen, QPixmap, QFont

from core.tag_ai_assist import GROUP_ACCENT_COLORS, chip_style_tokens


def _safe_point_size(font: QFont, delta: int = 0, minimum: int = 8) -> int:
    """避免 pointSize()==-1（像素字体）时 setPointSize 触发 Qt 警告。"""
    ps = font.pointSize()
    if ps is None or ps <= 0:
        px = font.pixelSize()
        if px and px > 0:
            ps = max(minimum, int(px * 0.75))
        else:
            ps = 10
    return max(minimum, ps + delta)


def _theme_from_palette(palette) -> str:
    try:
        return "dark" if palette.window().color().value() < 128 else "light"
    except Exception:
        return "dark"


def _tag_group_map_from_index(index) -> dict:
    """从行 UserRole 或 tag_groups 字段推断 tag→group_id。"""
    row = index.data(Qt.UserRole)
    if not isinstance(row, dict):
        src = index.model()
        if src and hasattr(src, "mapToSource"):
            try:
                src_idx = src.mapToSource(index)
                row = src_idx.model().data(src_idx, Qt.UserRole) if src_idx.model() else None
            except Exception:
                row = None
    mapping = {}
    if not isinstance(row, dict):
        return mapping
    tg = row.get("tag_groups")
    if isinstance(tg, dict):
        for gid, tags in tg.items():
            if not isinstance(tags, (list, tuple)):
                continue
            for t in tags:
                name = t.get("name") if isinstance(t, dict) else t
                if name:
                    mapping[str(name).strip()] = str(gid).strip().lower() or "custom"
    return mapping


def paint_material_tag_chips(
    painter: QPainter,
    rect: QRect,
    tags,
    *,
    tag_to_group=None,
    theme: str = "dark",
    max_chips: int = 6,
) -> None:
    """浅底 + 组色左边条的素材标签芯片（列表/卡片共用）。"""
    tag_to_group = tag_to_group or {}
    x = rect.left() + 2
    y = rect.top() + max(2, (rect.height() - 20) // 2)
    h = min(20, max(16, rect.height() - 4))
    fm = painter.fontMetrics()
    shown = list(tags or [])[:max_chips]
    extra = max(0, len(tags or []) - len(shown))
    for tag in shown:
        gid = tag_to_group.get(str(tag).strip(), "custom")
        tokens = chip_style_tokens(gid, theme=theme)
        text = str(tag)
        tw = fm.horizontalAdvance(text) + 14
        if x + tw > rect.right() - 4:
            break
        chip_rect = QRect(x, y, tw, h)
        path = QPainterPath()
        path.addRoundedRect(QRectF(chip_rect), 8, 8)
        painter.fillPath(path, QColor(tokens["background"]))
        # 左边色条
        bar = QRect(chip_rect.left(), chip_rect.top(), 3, chip_rect.height())
        painter.fillRect(bar, QColor(tokens["accent_bar"]))
        painter.setPen(QColor(tokens["text"]))
        painter.drawText(
            chip_rect.adjusted(8, 0, -4, 0),
            Qt.AlignVCenter | Qt.AlignLeft,
            text,
        )
        x += tw + 4
    if extra > 0 and x + 28 < rect.right():
        painter.setPen(QColor(chip_style_tokens("custom", theme=theme)["muted"]))
        painter.drawText(QRect(x, y, 40, h), Qt.AlignVCenter | Qt.AlignLeft, f"+{extra}")


class ThumbnailDelegate(QStyledItemDelegate):
    """表格视图缩略图绘制代理 (16:9 比例)"""
    # 与 VideoTableModel.COL_THUMB 一致（无选择列后为 2）
    THUMB_COL = 2

    def paint(self, painter, option, index):
        if not index.isValid():
            return

        pixmap = index.data(Qt.DecorationRole)
        if not isinstance(pixmap, QPixmap):
            pixmap = index.sibling(index.row(), self.THUMB_COL).data(Qt.DecorationRole)

        if isinstance(pixmap, QPixmap):
            painter.save()
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setRenderHint(QPainter.SmoothPixmapTransform)

            rect = option.rect.adjusted(2, 2, -2, -2)
            painter.fillRect(rect, Qt.black)

            scaled = pixmap.scaled(rect.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            x = rect.x() + (rect.width() - scaled.width()) // 2
            y = rect.y() + (rect.height() - scaled.height()) // 2
            painter.drawPixmap(x, y, scaled)

            painter.restore()
        else:
            super().paint(painter, option, index)

    def sizeHint(self, option, index):
        return QSize(120, 68)


class MaterialTagsColumnDelegate(QStyledItemDelegate):
    """列表「标签」列：浅底 + 组色条芯片。"""

    def paint(self, painter, option, index):
        if not index.isValid():
            return
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        if option.state & QStyle.State_Selected:
            painter.fillRect(option.rect, option.palette.highlight())
        tags_str = index.data(Qt.DisplayRole) or ""
        tags = [t.strip() for t in str(tags_str).split(",") if t.strip()]
        theme = _theme_from_palette(option.palette)
        paint_material_tag_chips(
            painter,
            option.rect.adjusted(2, 0, -2, 0),
            tags,
            tag_to_group=_tag_group_map_from_index(index),
            theme=theme,
            max_chips=8,
        )
        painter.restore()


class CardDelegate(QStyledItemDelegate):
    """网格视图卡片绘制代理：缩略图 + 列表序号/入库编号/文件名/分类/状态/标签芯片"""

    def __init__(self, parent=None):
        super().__init__(parent)

    def sizeHint(self, option, index):
        return QSize(230, 280)

    def paint(self, painter, option, index):
        if not index.isValid():
            return

        from gui.models.video_table import (
            COL_CATEGORY,
            COL_FILENAME,
            COL_LIBRARY_ID,
            COL_LIST_NO,
            COL_STATUS,
            COL_TAGS,
            COL_THUMB,
        )

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)

        rect = option.rect
        palette = option.palette

        bg = palette.window().color().lighter(105)
        if option.state & QStyle.State_Selected:
            bg = palette.highlight().color().lighter(150)
        elif option.state & QStyle.State_MouseOver:
            bg = palette.window().color().lighter(115)

        path = QPainterPath()
        path.addRoundedRect(QRectF(rect).adjusted(5, 5, -5, -5), 8, 8)
        painter.fillPath(path, bg)

        if option.state & QStyle.State_Selected:
            painter.setPen(QPen(palette.highlight().color(), 2))
            painter.drawPath(path)

        # 缩略图
        thumb_rect = QRect(rect.left() + 15, rect.top() + 12, rect.width() - 30, 120)
        painter.setBrush(QColor("black"))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(thumb_rect, 4, 4)

        thumb_index = index.sibling(index.row(), COL_THUMB)
        pixmap = thumb_index.data(Qt.DecorationRole)
        if isinstance(pixmap, QPixmap) and not pixmap.isNull():
            scaled = pixmap.scaled(
                thumb_rect.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation
            )
            crop_x = max(0, (scaled.width() - thumb_rect.width()) // 2)
            crop_y = max(0, (scaled.height() - thumb_rect.height()) // 2)
            painter.setClipRect(thumb_rect)
            painter.drawPixmap(
                thumb_rect.topLeft(),
                scaled,
                QRect(crop_x, crop_y, thumb_rect.width(), thumb_rect.height()),
            )
            painter.setClipping(False)

        # 文本信息
        list_no = index.sibling(index.row(), COL_LIST_NO).data(Qt.DisplayRole)
        lib_id = index.sibling(index.row(), COL_LIBRARY_ID).data(Qt.DisplayRole)
        filename = index.sibling(index.row(), COL_FILENAME).data(Qt.DisplayRole) or ""
        category = index.sibling(index.row(), COL_CATEGORY).data(Qt.DisplayRole) or ""
        status = index.sibling(index.row(), COL_STATUS).data(Qt.DisplayRole) or ""
        tags_str = index.sibling(index.row(), COL_TAGS).data(Qt.DisplayRole) or ""
        tag_parts = [t.strip() for t in str(tags_str).split(",") if t.strip()]

        painter.setPen(palette.windowText().color())
        font = QFont(painter.font())
        y = rect.top() + 140
        meta = f"#{list_no}  入库 {lib_id}"
        font.setBold(False)
        font.setPointSize(_safe_point_size(font, delta=-1, minimum=8))
        painter.setFont(font)
        painter.drawText(
            QRect(rect.left() + 15, y, rect.width() - 30, 18),
            Qt.AlignLeft | Qt.AlignVCenter,
            meta,
        )
        y += 18
        font.setBold(True)
        font.setPointSize(_safe_point_size(font, delta=1, minimum=9))
        painter.setFont(font)
        painter.drawText(
            QRect(rect.left() + 15, y, rect.width() - 30, 36),
            Qt.AlignLeft | Qt.AlignTop | Qt.TextWrapAnywhere,
            str(filename),
        )
        y += 38
        font.setBold(False)
        font.setPointSize(_safe_point_size(font, delta=-1, minimum=8))
        painter.setFont(font)
        painter.drawText(
            QRect(rect.left() + 15, y, rect.width() - 30, 18),
            Qt.AlignLeft | Qt.AlignVCenter,
            f"{category}  ·  {status}",
        )
        y += 20
        if tag_parts:
            theme = _theme_from_palette(palette)
            paint_material_tag_chips(
                painter,
                QRect(rect.left() + 12, y, rect.width() - 24, 36),
                tag_parts,
                tag_to_group=_tag_group_map_from_index(index.sibling(index.row(), COL_TAGS)),
                theme=theme,
                max_chips=3,
            )

        painter.restore()


class StatusDelegate(QStyledItemDelegate):
    """状态列彩色圆角标签委派"""

    def paint(self, painter, option, index):
        status = index.data(Qt.DisplayRole)
        status_norm = str(status).lower()

        color_map = {
            "待分析": ("#FFA500", "#FFF7E6"),
            "pending": ("#FFA500", "#FFF7E6"),
            "分析中": ("#1890FF", "#E6F7FF"),
            "已完成": ("#52C41A", "#F6FFED"),
            "analyzed": ("#52C41A", "#F6FFED"),
            "failed": ("#F5222D", "#FFF1F0"),
            "error": ("#F5222D", "#FFF1F0"),
            "edited": ("#722ED1", "#F9F0FF"),
            "xmp": ("#EB2F96", "#FFF0F6"),
            "renamed": ("#13C2C2", "#E6FFFB"),
        }

        border_color, bg_color = ("#8C8C8C", "#F5F5F5")
        for k, v in color_map.items():
            if k.lower() in status_norm:
                border_color, bg_color = v
                break

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)

        rect = option.rect.adjusted(4, 4, -4, -4)
        painter.setBrush(QBrush(QColor(bg_color)))
        painter.setPen(QPen(QColor(border_color), 1))
        painter.drawRoundedRect(rect, 8, 8)

        painter.setPen(QColor(border_color))
        painter.drawText(rect, Qt.AlignCenter, str(status))
        painter.restore()


class TagChipDelegate(QStyledItemDelegate):
    """
    负责绘制标签气泡的委托，支持主题自适应
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.chip_height = 28
        self.padding = 10
        self.delete_icon_size = 14

    def paint(self, painter, option, index):
        if not index.isValid():
            return

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)

        # 数据获取
        name = index.data(Qt.DisplayRole)
        usage_count = index.data(Qt.UserRole + 4) # TagListModel.USAGE_ROLE
        is_selected = option.state & QStyle.State_Selected
        is_hover = option.state & QStyle.State_MouseOver

        # 颜色
        if is_selected:
            bg_color = QColor("#3d5afe")
            text_color = QColor("white")
        else:
            bg_color = QColor("#e0e0e0") if not (option.palette.window().color().value() < 128) else QColor("#424242")
            # 尝试从模型获取自定义颜色
            custom_color = index.data(Qt.UserRole + 5) # COLOR_ROLE
            if custom_color:
                bg_color = QColor(custom_color)
            
            # 根据背景亮度决定文字颜色
            text_color = QColor("white") if bg_color.value() < 150 else QColor("black")

        rect = option.rect.adjusted(2, 2, -2, -2)
        
        # 绘制圆角矩形
        path = QPainterPath()
        path.addRoundedRect(QRectF(rect), 14, 14)
        painter.fillPath(path, bg_color)

        # 绘制文字
        font = painter.font()
        display_text = name
        if usage_count is not None and usage_count > 0:
            display_text = f"{name} ({usage_count})"
        
        text_rect = rect.adjusted(self.padding, 0, -self.padding, 0)
        if is_hover:
             text_rect.adjust(0, 0, -self.delete_icon_size - 5, 0)
             
        painter.setPen(text_color)
        elided_text = painter.fontMetrics().elidedText(display_text, Qt.ElideRight, text_rect.width())
        painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, elided_text)

        # 绘制删除按钮 (仅悬停时)
        if is_hover:
            del_rect = QRect(rect.right() - self.padding - self.delete_icon_size, 
                             rect.top() + (rect.height() - self.delete_icon_size) // 2,
                             self.delete_icon_size, self.delete_icon_size)
            painter.setPen(QPen(text_color, 2))
            painter.drawLine(del_rect.topLeft(), del_rect.bottomRight())
            painter.drawLine(del_rect.topRight(), del_rect.bottomLeft())

        painter.restore()

    def sizeHint(self, option, index):
        name = index.data(Qt.DisplayRole) or ""
        usage_count = index.data(Qt.UserRole + 4)
        display_text = name
        if usage_count is not None and usage_count > 0:
            display_text = f"{name} ({usage_count})"
        
        font_metrics = option.fontMetrics
        text_width = font_metrics.horizontalAdvance(display_text)
        width = text_width + self.padding * 2 + 5
        width += self.delete_icon_size + 5
        return QSize(width, self.chip_height)

    def editorEvent(self, event, model, option, index):
        """处理点击删除按钮事件"""
        if event.type() == QEvent.MouseButtonRelease:
            rect = option.rect.adjusted(2, 2, -2, -2)
            del_rect = QRect(rect.right() - self.padding - self.delete_icon_size - 2, 
                             rect.top(),
                             self.delete_icon_size + self.padding, rect.height())
            if del_rect.contains(event.pos()):
                # 获取原始模型并删除
                # 这里假设模型支持 removeRow
                return model.removeRow(index.row())
        return super().editorEvent(event, model, option, index)