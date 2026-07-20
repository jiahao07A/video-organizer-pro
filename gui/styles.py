# -*- coding: utf-8 -*-

def get_main_style(font_size=14, theme="dark"):
    """返回主界面的 QSS 样式，支持动态字体大小和主题切换"""
    
    if theme == "dark":
        colors = {
            "bg": "#1e1e1e",
            "sidebar_bg": "#252526",
            "text": "#d4d4d4",
            "secondary_text": "#808080",
            "border": "#3e3e42",
            "item_bg": "#2d2d2d",
            "accent": "#ce9178",
            "accent_hover": "#dfa891",
            "accent_pressed": "#b37d65",
            "splitter": "#3a3a3a"
        }
    else: # Light theme
        colors = {
            "bg": "#f3f3f3",
            "sidebar_bg": "#ffffff",
            "text": "#333333",
            "secondary_text": "#666666",
            "border": "#cccccc",
            "item_bg": "#ffffff",
            "accent": "#0078d4",
            "accent_hover": "#2b88d8",
            "accent_pressed": "#005a9e",
            "splitter": "#dddddd"
        }

    return f"""
        /* 全局基础设置 */
        QMainWindow, QDialog, QWidget {{
            background-color: {colors["bg"]};
            color: {colors["text"]};
            font-family: "Segoe UI", "Microsoft YaHei", sans-serif;
            font-size: {font_size}px;
        }}
        
        /* 侧边栏美化 */
        #sidebar {{
            background-color: {colors["sidebar_bg"]};
            border-right: 1px solid {colors["border"]};
        }}
        
        #sidebar QListWidget {{
            background: transparent;
            border: none;
        }}
        
        #sidebar QListWidget::item {{
            height: 45px;
            padding-left: 20px;
            margin: 4px 10px;
            border-radius: 6px;
            color: {colors["secondary_text"]};
        }}
        
        #sidebar QListWidget::item:hover {{
            background-color: {colors["accent"]}1A;
            color: {colors["text"]};
        }}
        
        #sidebar QListWidget::item:selected {{
            background-color: {colors["accent"]};
            color: #ffffff;
            font-weight: bold;
        }}
        
        /* 表格美化 */
        QTableView {{
            background-color: {colors["item_bg"]};
            gridline-color: {colors["border"]};
            border: 1px solid {colors["border"]};
            border-radius: 4px;
            selection-background-color: {colors["accent"]}33;
            selection-color: {colors["accent"]};
            color: {colors["text"]};
            outline: none;
        }}
        
        QHeaderView::section {{
            background-color: {colors["sidebar_bg"]};
            padding: 8px;
            border: none;
            border-bottom: 1px solid {colors["border"]};
            color: {colors["secondary_text"]};
            font-weight: bold;
        }}
        
        /* 分组框美化 */
        QGroupBox {{
            font-weight: bold;
            border: 1px solid {colors["border"]};
            border-radius: 8px;
            margin-top: 15px;
            padding-top: 15px;
            background-color: {colors["sidebar_bg"]};
            color: {colors["accent"]};
        }}
        
        QGroupBox::title {{
            subcontrol-origin: margin;
            subcontrol-position: top left;
            left: 15px;
            padding: 0 5px;
        }}
        
        /* 按钮美化 */
        QPushButton {{
            background-color: {colors["item_bg"]};
            border: 1px solid {colors["border"]};
            border-radius: 4px;
            color: {colors["text"]};
            padding: 6px 12px;
            min-height: 24px;
        }}
        
        QPushButton:hover {{
            background-color: {colors["accent"]}1A;
        }}
        
        QPushButton#primary_button {{
            background-color: {colors["accent"]};
            border: none;
            color: #ffffff;
            font-weight: bold;
        }}
        
        QPushButton#primary_button:hover {{
            background-color: {colors["accent_hover"]};
        }}
        
        QPushButton#primary_button:pressed {{
            background-color: {colors["accent_pressed"]};
        }}
        
        /* 输入框美化 */
        QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QComboBox {{
            background-color: {colors["item_bg"]};
            border: 1px solid {colors["border"]};
            border-radius: 4px;
            padding: 5px;
            color: {colors["text"]};
        }}
        
        QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QComboBox:focus {{
            border: 1px solid {colors["accent"]};
        }}
        
        /* 选项卡美化 */
        QTabWidget::pane {{
            border: 1px solid {colors["border"]};
            background-color: {colors["bg"]};
            border-radius: 4px;
            top: -1px;
        }}
        
        QTabBar::tab {{
            background-color: {colors["sidebar_bg"]};
            color: {colors["secondary_text"]};
            padding: 10px 20px;
            border: 1px solid {colors["border"]};
            border-bottom: none;
            border-top-left-radius: 4px;
            border-top-right-radius: 4px;
            margin-right: 2px;
        }}
        
        QTabBar::tab:selected {{
            background-color: {colors["bg"]};
            color: {colors["accent"]};
            border-bottom: 2px solid {colors["accent"]};
        }}
        
        QTabBar::tab:hover:!selected {{
            background-color: {colors["item_bg"]};
            color: {colors["text"]};
        }}
        
        /* 滚动条美化 */
        QScrollBar:vertical {{
            border: none;
            background: transparent;
            width: 10px;
            margin: 0;
        }}
        
        QScrollBar::handle:vertical {{
            background: {colors["border"]};
            min-height: 20px;
            border-radius: 5px;
        }}
        
        QScrollBar::handle:vertical:hover {{
            background: {colors["accent"]}66;
        }}
        
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
            height: 0px;
        }}
        
        /* 标签气泡样式 */
        #TagChip {{
            background-color: {colors["item_bg"]};
            border: 1px solid {colors["border"]};
            border-radius: 12px;
            color: {colors["text"]};
            padding: 2px 8px;
        }}
        
        /* 分隔条 */
        QSplitter::handle {{
            background-color: {colors["splitter"]};
        }}
    """


