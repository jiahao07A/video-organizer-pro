# -*- coding: utf-8 -*-
from PySide6.QtWidgets import QStyledItemDelegate, QStyle
from PySide6.QtCore import Qt, QSize, QRect, QRectF, QEvent
from PySide6.QtGui import QColor, QPainter, QPainterPath, QBrush, QPen, QPixmap

class ThumbnailDelegate(QStyledItemDelegate):
    """表格视图缩略图绘制代理 (16:9 比例)"""
    def paint(self, painter, option, index):
        if not index.isValid(): return
        
        # 确保从第1列（缩略图列）获取数据
        pixmap = index.data(Qt.DecorationRole)
        if not isinstance(pixmap, QPixmap):
            pixmap = index.sibling(index.row(), 1).data(Qt.DecorationRole)

        if isinstance(pixmap, QPixmap):
            painter.save()
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setRenderHint(QPainter.SmoothPixmapTransform)
            
            # 绘制黑色底背景
            rect = option.rect.adjusted(2, 2, -2, -2)
            painter.fillRect(rect, Qt.black)
            
            # 缩放并居中绘制图片
            scaled = pixmap.scaled(rect.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            x = rect.x() + (rect.width() - scaled.width()) // 2
            y = rect.y() + (rect.height() - scaled.height()) // 2
            painter.drawPixmap(x, y, scaled)
            
            painter.restore()
        else:
            super().paint(painter, option, index)

    def sizeHint(self, option, index):
        return QSize(120, 68) # 16:9 approx

class CardDelegate(QStyledItemDelegate):
    """网格视图卡片绘制代理，支持主题自适应"""
    def __init__(self, parent=None):
        super().__init__(parent)
        
    def sizeHint(self, option, index):
        return QSize(220, 240)

    def paint(self, painter, option, index):
        if not index.isValid(): return

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        
        rect = option.rect
        palette = option.palette
        
        # 状态背景
        bg = palette.window().color().lighter(105)
        if option.state & QStyle.State_Selected:
             bg = palette.highlight().color().lighter(150)
        elif option.state & QStyle.State_MouseOver:
             bg = palette.window().color().lighter(115)
             
        path = QPainterPath()
        path.addRoundedRect(QRectF(rect).adjusted(5,5,-5,-5), 8, 8)
        painter.fillPath(path, bg)
        
        # 绘制选中状态的细边框
        if option.state & QStyle.State_Selected:
            painter.setPen(QPen(palette.highlight().color(), 2))
            painter.drawPath(path)
        
        # 缩略图
        thumb_rect = QRect(rect.left()+15, rect.top()+15, rect.width()-30, 140)
        painter.setBrush(QColor("black"))
        painter.drawRoundedRect(thumb_rect, 4, 4)
        
        # 绘制图片 (从第一列请求 DecorationRole)
        thumb_index = index.sibling(index.row(), 1)
        pixmap = thumb_index.data(Qt.DecorationRole)
        if pixmap:
            scaled = pixmap.scaled(thumb_rect.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            # Center crop
            crop_x = (scaled.width() - thumb_rect.width()) // 2
            crop_y = (scaled.height() - thumb_rect.height()) // 2
            painter.setClipRect(thumb_rect)
            painter.drawPixmap(thumb_rect.topLeft(), scaled, QRect(crop_x, crop_y, thumb_rect.width(), thumb_rect.height()))
            painter.setClipping(False)

        # 文件名
        filename = index.data(Qt.DisplayRole)
        text_rect = QRect(rect.left()+15, rect.top()+165, rect.width()-30, 40)
        painter.setPen(palette.windowText().color())
        font = painter.font()
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(text_rect, Qt.AlignLeft | Qt.AlignTop | Qt.TextWrapAnywhere, filename)
        
        painter.restore()

class StatusDelegate(QStyledItemDelegate):
    """状态列彩色圆角标签委派"""
    def paint(self, painter, option, index):
        # 假设状态列在第5列（从0开始）
        # 实际使用时可以通过 setItemDelegateForColumn 指定，所以这里不再强行检查 column
        
        status = index.data(Qt.DisplayRole)
        # 兼容不同状态字符
        status_norm = str(status).lower()
        
        color_map = {
            "待分析": ("#FFA500", "#FFF7E6"), # 橙色
            "分析中": ("#1890FF", "#E6F7FF"), # 蓝色
            "已完成": ("#52C41A", "#F6FFED"), # 绿色
            "analyzed": ("#52C41A", "#F6FFED"),
            "edited": ("#722ED1", "#F9F0FF"), # 紫色
            "xmp": ("#EB2F96", "#FFF0F6"),    # 粉色
            "renamed": ("#13C2C2", "#E6FFFB"), # 青色
        }
        
        # 匹配映射
        border_color, bg_color = ("#8C8C8C", "#F5F5F5")
        for k, v in color_map.items():
            if k.lower() in status_norm:
                border_color, bg_color = v
                break
        
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        
        # 绘制背景
        rect = option.rect.adjusted(4, 4, -4, -4)
        painter.setBrush(QBrush(QColor(bg_color)))
        painter.setPen(QPen(QColor(border_color), 1))
        painter.drawRoundedRect(rect, 8, 8)
        
        # 绘制文字
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
        
        # 动态决定颜色 (从 palette 获取以支持主题切换)
        palette = option.palette
        bg_color = palette.window().color().lighter(120) if not is_selected else palette.highlight().color()
        text_color = palette.windowText().color() if not is_selected else palette.highlightedText().color()
        border_color = palette.mid().color() if not is_hover else palette.highlight().color()
        
        if is_hover and not is_selected:
            bg_color = bg_color.lighter(110)
            
        # 绘制背景
        rect = option.rect.adjusted(2, 2, -2, -2)
        path = QPainterPath()
        path.addRoundedRect(QRectF(rect), rect.height() / 2, rect.height() / 2)
        painter.fillPath(path, bg_color)
        
        # 绘制边框
        painter.setPen(QPen(border_color, 1.2 if is_hover or is_selected else 1))
        painter.drawPath(path)
        
        # 绘制文字
        painter.setPen(text_color)
        text_rect = rect.adjusted(self.padding, 0, -self.padding, 0)
        
        display_text = name
        if usage_count and usage_count > 0:
            display_text = f"{name} ({usage_count})"
            
        if is_hover:
             text_rect.adjust(0, 0, -self.delete_icon_size - 5, 0)
             
        # 开启省略号
        elided_text = painter.fontMetrics().elidedText(display_text, Qt.ElideRight, text_rect.width())
        painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, elided_text)
        
        # 绘制删除按钮 (仅悬停时)
        if is_hover:
            del_rect = QRect(rect.right() - self.padding - self.delete_icon_size, 
                             rect.top() + (rect.height() - self.delete_icon_size) // 2,
                             self.delete_icon_size, self.delete_icon_size)
            painter.setPen(QPen(QColor("#ff4d4f"), 1.5))
            # 绘制一个小 X
            m = 3
            painter.drawLine(del_rect.left() + m, del_rect.top() + m, del_rect.right() - m, del_rect.bottom() - m)
            painter.drawLine(del_rect.left() + m, del_rect.bottom() - m, del_rect.right() - m, del_rect.top() - m)

        painter.restore()

    def sizeHint(self, option, index):
        name = index.data(Qt.DisplayRole)
        usage_count = index.data(Qt.UserRole + 4)
        display_text = name
        if usage_count and usage_count > 0:
            display_text = f"{name} ({usage_count})"
            
        font_metrics = option.fontMetrics
        text_width = font_metrics.horizontalAdvance(display_text)
        width = text_width + (self.padding * 2)
        # 如果是悬停状态（在 QListView 中 sizeHint 无法直接感知 hover），我们预留一点空间
        width += self.delete_icon_size + 5
        return QSize(width, self.chip_height + 4)

    def editorEvent(self, event, model, option, index):
        """处理点击删除按钮事件"""
        if event.type() == QEvent.MouseButtonRelease:
            rect = option.rect
            del_rect = QRect(rect.right() - self.padding - self.delete_icon_size - 2, 
                             rect.top(),
                             self.delete_icon_size + self.padding, rect.height())
            
            if del_rect.contains(event.pos()):
                # 获取原始模型并删除
                source_model = model
                source_index = index
                
                # 处理代理模型层级
                while hasattr(source_model, "sourceModel"):
                    source_index = source_model.mapToSource(source_index)
                    source_model = source_model.sourceModel()
                
                if hasattr(source_model, "remove_tag"):
                    source_model.remove_tag(source_index.row())
                return True
        return super().editorEvent(event, model, option, index)

