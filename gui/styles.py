# -*- coding: utf-8 -*-
"""全局主题色板与主窗口 QSS。

light/dark 唯一语义：normalize_theme 后仅这两种。
局部面板（标签库、高级筛选）应使用 get_theme_colors，并在主题切换时 apply_theme。
"""


def normalize_theme(theme) -> str:
    """将配置中的主题值规范为 light 或 dark。

    仅显式 'light' 为浅色；dark / amber_gold / 其它历史值一律按 dark，
    避免 get_main_style 与局部色板映射不一致。
    """
    if theme is None:
        return "dark"
    t = str(theme).strip().lower()
    if t == "light":
        return "light"
    return "dark"


def get_theme_colors(theme="dark") -> dict:
    """返回当前主题的共享色板 token（主 QSS 与局部面板共用）。"""
    theme = normalize_theme(theme)
    if theme == "light":
        base = {
            "bg": "#f3f3f3",
            "sidebar_bg": "#ffffff",
            "text": "#333333",
            "secondary_text": "#666666",
            "border": "#cccccc",
            "item_bg": "#ffffff",
            "accent": "#0078d4",
            "accent_hover": "#2b88d8",
            "accent_pressed": "#005a9e",
            "splitter": "#dddddd",
            "danger": "#cf1322",
            "title": "#b45309",
            "heatmap_title": "#cf1322",
        }
    else:
        base = {
            "bg": "#1e1e1e",
            "sidebar_bg": "#252526",
            "text": "#d4d4d4",
            "secondary_text": "#808080",
            "border": "#3e3e42",
            "item_bg": "#2d2d2d",
            "accent": "#ce9178",
            "accent_hover": "#dfa891",
            "accent_pressed": "#b37d65",
            "splitter": "#3a3a3a",
            "danger": "#ff4d4f",
            "title": "#FFB300",
            "heatmap_title": "#ff4d4f",
        }

    # 局部面板别名（标签库分栏 / 筛选条 / 芯片）
    base.update({
        "panel_bg": base["sidebar_bg"] if theme == "light" else "#262626",
        "panel_border": base["border"],
        "input_bg": base["item_bg"] if theme == "light" else "#1f1f1f",
        "input_text": base["text"],
        "muted": base["secondary_text"],
        "heatmap_bg": base["bg"] if theme == "light" else "#1a1a1a",
        "heatmap_border": base["border"],
        "chip_bg": "#ebebeb" if theme == "light" else "#3a3a3a",
        "chip_text": base["text"] if theme == "light" else "#e8e8e8",
        "chip_border": base["border"] if theme == "light" else "#555555",
        "list_bg": "#fafafa" if theme == "light" else "#1f1f1f",
        "filter_bg": base["sidebar_bg"],
    })
    return base


def get_main_style(font_size=14, theme="dark"):
    """返回主界面的 QSS 样式，支持动态字体大小和主题切换"""
    colors = get_theme_colors(theme)

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
        QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QComboBox, QDateEdit {{
            background-color: {colors["item_bg"]};
            border: 1px solid {colors["border"]};
            border-radius: 4px;
            padding: 5px;
            color: {colors["text"]};
        }}
        
        QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QComboBox:focus, QDateEdit:focus {{
            border: 1px solid {colors["accent"]};
        }}
        
        QCheckBox {{
            color: {colors["text"]};
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

        /* 高级筛选面板 */
        #FilterPanel {{
            background-color: {colors["filter_bg"]};
            border: 1px solid {colors["border"]};
            border-radius: 8px;
            color: {colors["text"]};
        }}
        #FilterPanel QLabel {{
            color: {colors["text"]};
            background: transparent;
        }}
    """