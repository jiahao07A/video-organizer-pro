# -*- coding: utf-8 -*-
import os
import threading
import json
import importlib.util
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import List, Dict, Optional, Any
from PIL import Image

import customtkinter as ctk

# 导入后端逻辑
from video_organizer import (
    VideoOrganizer, SettingsManager, DatabaseManager,
    UNDO_FILE, RESULTS_FILE_JSON, RESULTS_FILE_CSV, DB_FILE
)

# 设置外观
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")


class VideoOrganizerGUI(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("AI Video Organizer v3.0 (Mega Update)")
        self.geometry("1300x850")

        # 初始化数据库与设置
        self.db = DatabaseManager(DB_FILE)
        self.settings = SettingsManager.load_settings(self.db)

        # 字体初始化
        self.default_font_size = self.settings.get("font_size", 14)
        self.update_fonts()

        # 变量初始化
        self.input_path_var = tk.StringVar()
        self.status_var = tk.StringVar(value="准备就绪")
        self.progress_var = tk.DoubleVar(value=0)
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *args: self.refresh_table())

        self.thumbnail_cache = {}
        self.selected_item_data = None
        self.all_video_data = []  # 缓存所有数据用于搜索

        self.setup_ui()
        self.load_settings_to_ui()

    def update_fonts(self):
        """更新全局字体设置"""
        size = self.default_font_size
        self.main_font = ctk.CTkFont(size=size)
        self.bold_font = ctk.CTkFont(size=size, weight="bold")
        self.title_font = ctk.CTkFont(size=int(size * 1.5), weight="bold")
        self.small_font = ctk.CTkFont(size=int(size * 0.8))

        # 更新 Treeview 样式 (ttk)
        style = ttk.Style()
        # 根据当前模式调整背景/前景
        is_dark = ctk.get_appearance_mode() == "Dark"
        bg_color = "#2b2b2b" if is_dark else "#dbdbdb"
        fg_color = "white" if is_dark else "black"
        
        style.configure("Treeview", font=(None, size), rowheight=int(size * 2))
        style.configure("Treeview.Heading", font=(None, size, "bold"))

    def setup_ui(self):
        # 创建左侧导航栏
        self.navigation_frame = ctk.CTkFrame(self, corner_radius=0)
        self.navigation_frame.grid(row=0, column=0, sticky="nsew")
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)

        self.navigation_frame_label = ctk.CTkLabel(self.navigation_frame, text="AI MAM v3",
                                                   font=self.title_font)
        self.navigation_frame_label.grid(row=0, column=0, padx=20, pady=20)

        self.home_button = ctk.CTkButton(self.navigation_frame, corner_radius=0, height=40, border_spacing=10, text="工作台",
                                         fg_color="transparent", text_color=("gray10", "gray90"), hover_color=("gray70", "gray30"),
                                         anchor="w", font=self.main_font, command=self.home_button_event)
        self.home_button.grid(row=1, column=0, sticky="ew")

        self.tags_button = ctk.CTkButton(self.navigation_frame, corner_radius=0, height=40, border_spacing=10, text="标签库",
                                         fg_color="transparent", text_color=("gray10", "gray90"), hover_color=("gray70", "gray30"),
                                         anchor="w", font=self.main_font, command=self.tags_button_event)
        self.tags_button.grid(row=2, column=0, sticky="ew")

        self.settings_button = ctk.CTkButton(self.navigation_frame, corner_radius=0, height=40, border_spacing=10, text="设置",
                                             fg_color="transparent", text_color=("gray10", "gray90"), hover_color=("gray70", "gray30"),
                                             anchor="w", font=self.main_font, command=self.settings_button_event)
        self.settings_button.grid(row=3, column=0, sticky="ew")

        self.appearance_mode_menu = ctk.CTkOptionMenu(self.navigation_frame, values=["Light", "Dark", "System"],
                                                      font=self.main_font, command=self.change_appearance_mode_event)
        self.appearance_mode_menu.grid(
            row=6, column=0, padx=20, pady=20, sticky="s")

        # 创建页面容器
        self.home_frame = ctk.CTkFrame(
            self, corner_radius=0, fg_color="transparent")
        self.tags_frame = ctk.CTkFrame(
            self, corner_radius=0, fg_color="transparent")
        self.settings_frame = ctk.CTkFrame(
            self, corner_radius=0, fg_color="transparent")

        self.setup_home_frame()
        self.setup_tags_frame()
        self.setup_settings_frame()

        # 底部状态栏
        self.status_bar = ctk.CTkFrame(self, height=30, corner_radius=0)
        self.status_bar.grid(row=1, column=0, columnspan=2, sticky="ew")

        self.status_label = ctk.CTkLabel(
            self.status_bar, textvariable=self.status_var, font=self.small_font)
        self.status_label.pack(side="left", padx=20)

        self.progress_bar = ctk.CTkProgressBar(
            self.status_bar, variable=self.progress_var)
        self.progress_bar.pack(side="right", padx=20, fill="x", expand=True)
        self.progress_bar.set(0)

        # 默认显示首页
        self.select_frame_by_name("home")

    def setup_home_frame(self):
        self.home_frame.grid_columnconfigure(0, weight=1)
        self.home_frame.grid_rowconfigure(1, weight=1)

        # 顶部工具栏
        top_bar = ctk.CTkFrame(self.home_frame, fg_color="transparent")
        top_bar.grid(row=0, column=0, sticky="ew", padx=20, pady=10)

        self.entry_path = ctk.CTkEntry(
            top_bar, textvariable=self.input_path_var, placeholder_text="选择视频文件夹...", width=300, font=self.main_font)
        self.entry_path.pack(side="left", padx=(0, 10))

        self.btn_browse = ctk.CTkButton(
            top_bar, text="浏览", width=80, font=self.main_font, command=self.browse_path)
        self.btn_browse.pack(side="left", padx=5)

        self.btn_analyze = ctk.CTkButton(
            top_bar, text="开始分析", width=100, font=self.main_font, command=self.start_analysis_thread)
        self.btn_analyze.pack(side="left", padx=5)

        # V3: 搜索栏
        search_frame = ctk.CTkFrame(top_bar, fg_color="transparent")
        search_frame.pack(side="right", padx=10)
        ctk.CTkLabel(search_frame, text="搜索:", font=self.main_font).pack(side="left", padx=5)
        self.search_entry = ctk.CTkEntry(
            search_frame, textvariable=self.search_var, placeholder_text="过滤文件名/标签...", font=self.main_font)
        self.search_entry.pack(side="left")

        # 主体：列表与详情
        main_content = ctk.CTkFrame(self.home_frame, fg_color="transparent")
        main_content.grid(row=1, column=0, sticky="nsew", padx=20, pady=10)
        main_content.grid_columnconfigure(0, weight=3)
        main_content.grid_columnconfigure(1, weight=1)
        main_content.grid_rowconfigure(0, weight=1)

        # 左侧表格 (使用标准 Treeview 嵌入)
        table_container = ctk.CTkFrame(main_content)
        table_container.grid(row=0, column=0, sticky="nsew", padx=(0, 10))

        # V3: 增加选择框列 (使用 Unicode 模拟)
        columns = ("select", "filename", "category", "tags", "status", "dup")
        self.tree = ttk.Treeview(
            table_container, columns=columns, show="headings")
        self.tree.heading("select", text="[ ]")
        self.tree.heading("filename", text="文件名")
        self.tree.heading("category", text="分类")
        self.tree.heading("tags", text="标签")
        self.tree.heading("status", text="状态")
        self.tree.heading("dup", text="去重")

        self.tree.column("select", width=40, anchor="center")
        self.tree.column("filename", width=250)
        self.tree.column("category", width=100)
        self.tree.column("tags", width=200)
        self.tree.column("status", width=80)
        self.tree.column("dup", width=60)

        scrollbar = ttk.Scrollbar(
            table_container, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)

        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self.on_tree_select)
        self.tree.bind("<Button-1>", self.on_tree_click)  # 用于点击复选框

        # 右侧详情面板
        self.details_panel = ctk.CTkScrollableFrame(
            main_content, label_text="视频详情与编辑")
        self.details_panel.grid(row=0, column=1, sticky="nsew")

        # 批量操作提示标签
        self.batch_info_label = ctk.CTkLabel(
            self.details_panel, text="", font=self.bold_font, text_color="#3B8ED0")
        self.batch_info_label.pack(fill="x", pady=(0, 5))

        self.thumb_label = ctk.CTkLabel(
            self.details_panel, text="预览图\n(双击播放)", height=150, fg_color="gray30", corner_radius=6, font=self.main_font)
        self.thumb_label.pack(fill="x", pady=10)
        self.thumb_label.bind("<Double-Button-1>", lambda e: self.play_video())

        ctk.CTkLabel(self.details_panel, text="分类:", anchor="w", font=self.main_font).pack(fill="x")
        self.edit_category = ctk.CTkComboBox(
            self.details_panel, values=self.settings.get("categories", []), font=self.main_font)
        self.edit_category.pack(fill="x", pady=(0, 10))

        ctk.CTkLabel(self.details_panel, text="摘要:", anchor="w", font=self.main_font).pack(fill="x")
        self.edit_summary = ctk.CTkTextbox(self.details_panel, height=80, font=self.main_font)
        self.edit_summary.pack(fill="x", pady=(0, 10))

        ctk.CTkLabel(self.details_panel, text="标签 (逗号分隔):",
                     anchor="w", font=self.main_font).pack(fill="x")
        self.edit_tags = ctk.CTkEntry(self.details_panel, font=self.main_font)
        self.edit_tags.pack(fill="x", pady=(0, 10))

        # V3: 转录结果显示
        ctk.CTkLabel(self.details_panel, text="音频转录:",
                     anchor="w", font=self.main_font).pack(fill="x")
        self.trans_text = ctk.CTkTextbox(self.details_panel, height=80, font=self.main_font)
        self.trans_text.pack(fill="x", pady=(0, 10))

        self.merge_tags_var = tk.BooleanVar(value=True)
        self.merge_tags_checkbox = ctk.CTkCheckBox(
            self.details_panel, text="合并标签 (避免覆盖原有标签)", variable=self.merge_tags_var, font=self.small_font)
        self.merge_tags_checkbox.pack(fill="x", pady=5)

        self.btn_save_edit = ctk.CTkButton(
            self.details_panel, text="保存修改", font=self.main_font, command=self.save_manual_edits)
        self.btn_save_edit.pack(fill="x", pady=5)

        # 底部操作按钮
        bottom_bar = ctk.CTkFrame(self.home_frame, fg_color="transparent")
        bottom_bar.grid(row=2, column=0, sticky="ew", padx=20, pady=10)

        ctk.CTkButton(bottom_bar, text="模拟重命名", fg_color="gray40", width=100, font=self.main_font,
                      command=lambda: self.start_rename_thread(dry_run=True)).pack(side="left", padx=5)
        ctk.CTkButton(bottom_bar, text="应用重命名", width=100, font=self.main_font, command=lambda: self.start_rename_thread(
            dry_run=False)).pack(side="left", padx=5)
        ctk.CTkButton(bottom_bar, text="数据库回滚", fg_color="darkred", width=100, font=self.main_font,
                      command=self.rollback_history).pack(side="left", padx=5)

        # V3: 导出与批量操作
        ctk.CTkButton(bottom_bar, text="导出 FCPX XML", fg_color="green", width=120, font=self.main_font,
                      command=self.export_xml_ui).pack(side="left", padx=5)

        self.btn_select_all = ctk.CTkButton(bottom_bar, text="全选/全不选", width=100, fg_color="gray30", font=self.main_font,
                                            command=self.toggle_select_all)
        self.btn_select_all.pack(side="right", padx=5)

        ctk.CTkButton(bottom_bar, text="刷新", width=60, font=self.main_font,
                      command=self.refresh_table).pack(side="right", padx=5)

    def setup_tags_frame(self):
        self.tags_frame.grid_columnconfigure((0, 1, 2), weight=1)
        self.tags_frame.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(self.tags_frame, text="标签库管理", font=self.bold_font).grid(row=0, column=0, columnspan=3, pady=20)

        # 分类管理
        cat_frame = ctk.CTkFrame(self.tags_frame)
        cat_frame.grid(row=1, column=0, sticky="nsew", padx=10, pady=10)
        ctk.CTkLabel(cat_frame, text="主分类", font=self.main_font).pack(pady=5)
        self.cat_listbox = tk.Listbox(
            cat_frame, bg="#2b2b2b", fg="white", borderwidth=0, highlightthickness=0, font=("TkDefaultFont", self.default_font_size))
        self.cat_listbox.pack(fill="both", expand=True, padx=5, pady=5)
        ctk.CTkButton(cat_frame, text="添加", height=24, font=self.main_font, command=lambda: self.add_tag_ui(
            "Categories")).pack(fill="x", padx=5, pady=2)
        ctk.CTkButton(cat_frame, text="删除", height=24, fg_color="darkred", font=self.main_font,
                      command=lambda: self.delete_tag_ui("Categories")).pack(fill="x", padx=5, pady=2)

        # 维度管理
        dim_frame = ctk.CTkFrame(self.tags_frame)
        dim_frame.grid(row=1, column=1, sticky="nsew", padx=10, pady=10)
        ctk.CTkLabel(dim_frame, text="标签维度", font=self.main_font).pack(pady=5)
        self.dim_listbox = tk.Listbox(
            dim_frame, bg="#2b2b2b", fg="white", borderwidth=0, highlightthickness=0, font=("TkDefaultFont", self.default_font_size))
        self.dim_listbox.pack(fill="both", expand=True, padx=5, pady=5)
        self.dim_listbox.bind("<<ListboxSelect>>", self.on_dim_select_ui)
        ctk.CTkButton(dim_frame, text="添加维度", height=24, font=self.main_font,
                      command=self.add_dimension_ui).pack(fill="x", padx=5, pady=2)

        # 标签内容
        tag_content_frame = ctk.CTkFrame(self.tags_frame)
        tag_content_frame.grid(
            row=1, column=2, sticky="nsew", padx=10, pady=10)
        self.tag_content_label = ctk.CTkLabel(tag_content_frame, text="维度内容", font=self.main_font)
        self.tag_content_label.pack(pady=5)
        self.tag_val_listbox = tk.Listbox(
            tag_content_frame, bg="#2b2b2b", fg="white", borderwidth=0, highlightthickness=0, font=("TkDefaultFont", self.default_font_size))
        self.tag_val_listbox.pack(fill="both", expand=True, padx=5, pady=5)
        ctk.CTkButton(tag_content_frame, text="添加标签", height=24, font=self.main_font,
                      command=lambda: self.add_tag_ui(None)).pack(fill="x", padx=5, pady=2)
        ctk.CTkButton(tag_content_frame, text="合并标签", height=24, fg_color="gray40", font=self.main_font,
                      command=lambda: self.merge_tag_ui(None)).pack(fill="x", padx=5, pady=2)
        ctk.CTkButton(tag_content_frame, text="删除标签", height=24, fg_color="darkred", font=self.main_font,
                      command=lambda: self.delete_tag_ui(None)).pack(fill="x", padx=5, pady=2)

    def setup_settings_frame(self):
        self.settings_frame.grid_columnconfigure(0, weight=1)

        tabview = ctk.CTkTabview(self.settings_frame)
        tabview.pack(fill="both", expand=True, padx=20, pady=20)

        tab_api = tabview.add("API设置")
        tab_v3 = tabview.add("V3功能")
        tab_perf = tabview.add("性能设置")
        tab_ui = tabview.add("个性化")

        # API设置
        self.api_key_var = tk.StringVar(value=self.settings.get("api_key", ""))
        self.base_url_var = tk.StringVar(
            value=self.settings.get("base_url", ""))
        self.model_name_var = tk.StringVar(
            value=self.settings.get("model_name", ""))

        ctk.CTkLabel(tab_api, text="OpenAI API Key:").pack(
            anchor="w", padx=10, pady=(10, 0))
        ctk.CTkEntry(tab_api, textvariable=self.api_key_var,
                     width=400, show="*").pack(anchor="w", padx=10, pady=5)
        ctk.CTkLabel(tab_api, text="Base URL:").pack(
            anchor="w", padx=10, pady=(10, 0))
        ctk.CTkEntry(tab_api, textvariable=self.base_url_var,
                     width=400).pack(anchor="w", padx=10, pady=5)
        ctk.CTkLabel(tab_api, text="Model Name:").pack(
            anchor="w", padx=10, pady=(10, 0))
        ctk.CTkEntry(tab_api, textvariable=self.model_name_var,
                     width=400).pack(anchor="w", padx=10, pady=5)

        # V3功能设置
        self.consensus_mode_var = tk.BooleanVar(
            value=self.settings.get("consensus_mode", False))
        self.sec_model_var = tk.StringVar(
            value=self.settings.get("secondary_model", "gpt-4o-mini"))
        self.enable_scene_var = tk.BooleanVar(
            value=self.settings.get("enable_scene_detection", True))
        self.enable_audio_var = tk.BooleanVar(
            value=self.settings.get("enable_audio_transcription", False))
        self.enable_meta_var = tk.BooleanVar(
            value=self.settings.get("enable_metadata_injection", False))

        ctk.CTkSwitch(tab_v3, text="开启多模型共识模式", variable=self.consensus_mode_var).pack(
            anchor="w", padx=10, pady=10)
        ctk.CTkLabel(tab_v3, text="次要模型:").pack(anchor="w", padx=30)
        ctk.CTkEntry(tab_v3, textvariable=self.sec_model_var,
                     width=200).pack(anchor="w", padx=30, pady=5)

        ctk.CTkSwitch(tab_v3, text="开启智能场景检测 (需 PySceneDetect)",
                      variable=self.enable_scene_var).pack(anchor="w", padx=10, pady=10)
        ctk.CTkSwitch(tab_v3, text="开启音频转录 (Whisper API)",
                      variable=self.enable_audio_var).pack(anchor="w", padx=10, pady=10)
        ctk.CTkSwitch(tab_v3, text="开启元数据注入 (XMP/FFmpeg)",
                      variable=self.enable_meta_var).pack(anchor="w", padx=10, pady=10)

        # 性能设置
        self.max_workers_var = tk.StringVar(
            value=str(self.settings.get("max_workers", 4)))
        self.max_frames_var = tk.StringVar(
            value=str(self.settings.get("max_frames", 10)))
        ctk.CTkLabel(tab_perf, text="并发线程数:").pack(
            anchor="w", padx=10, pady=(10, 0))
        ctk.CTkEntry(tab_perf, textvariable=self.max_workers_var,
                     width=100).pack(anchor="w", padx=10, pady=5)
        ctk.CTkLabel(tab_perf, text="分析抽帧数:").pack(
            anchor="w", padx=10, pady=(10, 0))
        ctk.CTkEntry(tab_perf, textvariable=self.max_frames_var,
                     width=100).pack(anchor="w", padx=10, pady=5)

        # 个性化
        self.rename_pattern_var = tk.StringVar(value=self.settings.get(
            "rename_pattern", "{category}-{tags}-{summary}-{original_name}"))
        self.font_size_var = tk.IntVar(value=self.default_font_size)

        ctk.CTkLabel(tab_ui, text="重命名模板:").pack(
            anchor="w", padx=10, pady=(10, 0))
        ctk.CTkEntry(tab_ui, textvariable=self.rename_pattern_var,
                     width=500).pack(anchor="w", padx=10, pady=5)
        ctk.CTkLabel(tab_ui, text="可用变量: {category}, {tags}, {summary}, {original_name}", font=ctk.CTkFont(
            size=11)).pack(anchor="w", padx=10)

        ctk.CTkLabel(tab_ui, text="界面字体大小 (12-24):", font=self.main_font).pack(
            anchor="w", padx=10, pady=(20, 0))
        
        font_slider_frame = ctk.CTkFrame(tab_ui, fg_color="transparent")
        font_slider_frame.pack(anchor="w", padx=10, pady=5, fill="x")
        
        self.font_slider = ctk.CTkSlider(font_slider_frame, from_=12, to=24, number_of_steps=12,
                                         variable=self.font_size_var)
        self.font_slider.pack(side="left", padx=(0, 10))
        
        self.font_size_label = ctk.CTkLabel(font_slider_frame, textvariable=self.font_size_var, font=self.main_font)
        self.font_size_label.pack(side="left")

        ctk.CTkButton(self.settings_frame, text="保存设置",
                      command=self.save_settings_from_ui).pack(pady=20)

    def save_settings_from_ui(self):
        try:
            self.settings["api_key"] = self.api_key_var.get()
            self.settings["base_url"] = self.base_url_var.get()
            self.settings["model_name"] = self.model_name_var.get()
            self.settings["consensus_mode"] = self.consensus_mode_var.get()
            self.settings["secondary_model"] = self.sec_model_var.get()
            self.settings["enable_scene_detection"] = self.enable_scene_var.get()
            self.settings["enable_audio_transcription"] = self.enable_audio_var.get()
            self.settings["enable_metadata_injection"] = self.enable_meta_var.get()
            self.settings["max_workers"] = int(self.max_workers_var.get())
            self.settings["max_frames"] = int(self.max_frames_var.get())
            self.settings["rename_pattern"] = self.rename_pattern_var.get()
            self.settings["font_size"] = self.font_size_var.get()

            SettingsManager.save_settings(self.settings, self.db)
            messagebox.showinfo("成功", "设置已同步至数据库，重启程序以应用新字体大小")
        except Exception as e:
            messagebox.showerror("错误", f"保存失败: {e}")

    def select_frame_by_name(self, name):
        # 更新按钮颜色
        self.home_button.configure(
            fg_color=("gray75", "gray25") if name == "home" else "transparent")
        self.tags_button.configure(
            fg_color=("gray75", "gray25") if name == "tags" else "transparent")
        self.settings_button.configure(
            fg_color=("gray75", "gray25") if name == "settings" else "transparent")

        # 显示页面
        if name == "home":
            self.home_frame.grid(row=0, column=1, sticky="nsew")
        else:
            self.home_frame.grid_forget()
        if name == "tags":
            self.tags_frame.grid(row=0, column=1, sticky="nsew")
            self.refresh_tags_ui()
        else:
            self.tags_frame.grid_forget()
        if name == "settings":
            self.settings_frame.grid(row=0, column=1, sticky="nsew")
        else:
            self.settings_frame.grid_forget()

    def home_button_event(self):
        self.select_frame_by_name("home")

    def tags_button_event(self):
        self.select_frame_by_name("tags")

    def settings_button_event(self):
        self.select_frame_by_name("settings")

    def change_appearance_mode_event(self, new_appearance_mode):
        ctk.set_appearance_mode(new_appearance_mode)

    def browse_path(self):
        path = filedialog.askdirectory()
        if path:
            self.input_path_var.set(path)

    def on_tree_click(self, event):
        """处理点击复选框列"""
        region = self.tree.identify_region(event.x, event.y)
        if region == "cell":
            column = self.tree.identify_column(event.x)
            if column == "#1":  # 第一列 select
                item_id = self.tree.identify_row(event.y)
                current_values = list(self.tree.item(item_id, "values"))
                current_values[0] = "[x]" if current_values[0] == "[ ]" else "[ ]"
                self.tree.item(item_id, values=current_values)

    def toggle_select_all(self):
        items = self.tree.get_children()
        if not items:
            return
        first_val = self.tree.item(items[0], "values")[0]
        new_val = "[x]" if first_val == "[ ]" else "[ ]"
        for item in items:
            vals = list(self.tree.item(item, "values"))
            vals[0] = new_val
            self.tree.item(item, values=vals)

    def refresh_table(self):
        for item in self.tree.get_children():
            self.tree.delete(item)

        self.all_video_data = self.db.get_all_videos()
        search_query = self.search_var.get().lower()

        phash_counts = {}
        for item in self.all_video_data:
            ph = item.get("phash", "")
            if ph:
                phash_counts[ph] = phash_counts.get(ph, 0) + 1

        for item in self.all_video_data:
            filename = item.get("filename", "")
            tags = item.get("tags", [])
            category = item.get("category", "")

            if search_query and not (search_query in filename.lower() or
                                     any(search_query in t.lower() for t in tags) or
                                     search_query in category.lower()):
                continue

            status = item.get("status", "analyzed")
            if item.get("manual_override"):
                status = "edited"
            if item.get("metadata_injected"):
                status = "XMP"

            ph = item.get("phash", "")
            dup_status = "重复" if ph and phash_counts.get(ph, 0) > 1 else "-"

            self.tree.insert("", tk.END, values=(
                "[ ]",
                filename,
                category,
                ", ".join(tags),
                status,
                dup_status
            ))

    def on_tree_select(self, event):
        selected = self.tree.selection()
        if not selected:
            self.selected_item_data = None
            self.batch_info_label.configure(text="")
            return

        selected_items = []
        for s in selected:
            filename = self.tree.item(s, "values")[1]
            for item in self.all_video_data:
                if item.get("filename") == filename:
                    selected_items.append(item)
                    break
        
        if len(selected_items) > 1:
            self.selected_item_data = selected_items
            self.batch_info_label.configure(text=f"正在批量编辑 {len(selected_items)} 个视频")
            # 批量模式下，清空输入框或显示共有项（简单起见，这里清空并允许输入）
            self.update_details_panel_multi(selected_items)
        elif len(selected_items) == 1:
            self.selected_item_data = selected_items[0]
            self.batch_info_label.configure(text="")
            self.update_details_panel(selected_items[0])

    def update_details_panel_multi(self, items):
        self.thumb_label.configure(image=None, text="多选模式\n(预览不可用)")
        self.edit_category.set("")
        self.edit_summary.delete("1.0", tk.END)
        self.edit_summary.insert(tk.END, "(批量编辑模式下不支持修改摘要)")
        self.edit_summary.configure(state="disabled")
        self.edit_tags.delete(0, tk.END)
        self.trans_text.delete("1.0", tk.END)
        self.trans_text.insert(tk.END, "(批量编辑模式下不支持修改转录)")
        self.trans_text.configure(state="disabled")

    def update_details_panel(self, item):
        self.edit_summary.configure(state="normal")
        self.trans_text.configure(state="normal")
        thumb_path = item.get("thumbnail_path")
        if thumb_path and os.path.exists(thumb_path):
            try:
                if thumb_path not in self.thumbnail_cache:
                    img = Image.open(thumb_path)
                    img.thumbnail((300, 200))
                    self.thumbnail_cache[thumb_path] = ctk.CTkImage(
                        light_image=img, dark_image=img, size=(240, 160))
                self.thumb_label.configure(
                    image=self.thumbnail_cache[thumb_path], text="")
            except Exception:
                self.thumb_label.configure(image=None, text="缩略图加载失败")
        else:
            self.thumb_label.configure(image=None, text="无缩略图")

        self.edit_category.set(item.get("category", ""))
        self.edit_summary.delete("1.0", tk.END)
        self.edit_summary.insert(tk.END, item.get("summary", ""))
        self.edit_tags.delete(0, tk.END)
        self.edit_tags.insert(0, ", ".join(item.get("tags", [])))

        # V3 转录显示
        self.trans_text.delete("1.0", tk.END)
        self.trans_text.insert(tk.END, item.get("transcription", ""))

    def rollback_history(self):
        if messagebox.askyesno("回滚确认", "确定要撤销上一组重命名吗？"):
            organizer = VideoOrganizer(self.settings)
            if organizer.rollback_last_session():
                self.refresh_table()
                messagebox.showinfo("成功", "回滚成功")
            else:
                messagebox.showwarning("提示", "没有可回滚的记录")

    def export_xml_ui(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".xml", filetypes=[("FCPX XML", "*.xml")])
        if path:
            organizer = VideoOrganizer(self.settings)
            organizer.export_to_fcpx_xml(path)
            messagebox.showinfo("成功", f"导出至 {path}")

    def save_manual_edits(self):
        if not self.selected_item_data:
            messagebox.showwarning("警告", "请先选择视频")
            return

        new_cat = self.edit_category.get()
        new_tags_input = [t.strip() for t in self.edit_tags.get().split(",") if t.strip()]
        merge_tags = self.merge_tags_var.get()

        if isinstance(self.selected_item_data, list):
            # 批量更新
            count = 0
            for item in self.selected_item_data:
                # 复制原有数据
                updates = item.copy()
                if new_cat:
                    updates["category"] = new_cat
                
                if new_tags_input:
                    if merge_tags:
                        # 合并标签并去重
                        existing_tags = set(item.get("tags", []))
                        updates["tags"] = list(existing_tags.union(set(new_tags_input)))
                    else:
                        updates["tags"] = new_tags_input
                
                updates["manual_override"] = True
                self.db.upsert_video(updates)
                count += 1
            self.refresh_table()
            messagebox.showinfo("成功", f"批量更新完成，共更新 {count} 个视频")
        else:
            # 单个更新
            item = self.selected_item_data
            updates = item.copy()
            updates["category"] = new_cat
            updates["summary"] = self.edit_summary.get("1.0", tk.END).strip()
            
            if merge_tags:
                existing_tags = set(item.get("tags", []))
                updates["tags"] = list(existing_tags.union(set(new_tags_input)))
            else:
                updates["tags"] = new_tags_input
                
            updates["transcription"] = self.trans_text.get("1.0", tk.END).strip()
            updates["manual_override"] = True
            
            self.db.upsert_video(updates)
            self.refresh_table()
            messagebox.showinfo("成功", "修改已保存")

    def play_video(self):
        if not self.selected_item_data or isinstance(self.selected_item_data, list):
            return
        
        video_path = self.selected_item_data.get("path")
        if video_path and os.path.exists(video_path):
            try:
                os.startfile(video_path)
            except Exception as e:
                messagebox.showerror("错误", f"无法播放视频: {e}")
        else:
            messagebox.showwarning("警告", "视频文件不存在")

    def start_analysis_thread(self):
        path = self.input_path_var.get()
        if not path or not os.path.exists(path):
            messagebox.showwarning("警告", "请输入有效的路径")
            return

        self.status_var.set("正在分析...")
        self.progress_var.set(0)

        def update_status(m):
            self.after(0, lambda: self.status_var.set(m))

        def update_progress(c, t):
            self.after(0, lambda: self.progress_var.set(c/t if t > 0 else 1))

        def run():
            organizer = VideoOrganizer(
                self.settings,
                on_log=update_status,
                on_progress=update_progress
            )
            organizer.run_analysis(path)
            self.after(0, self.refresh_table)
            self.after(0, lambda: self.status_var.set("分析完成"))

        threading.Thread(target=run, daemon=True).start()

    def start_rename_thread(self, dry_run=True):
        # V3: 支持获取选中的文件
        selected_paths = []
        for item in self.tree.get_children():
            vals = self.tree.item(item, "values")
            if vals[0] == "[x]":
                filename = vals[1]
                # 寻找对应的完整路径
                for v in self.all_video_data:
                    if v.get("filename") == filename:
                        selected_paths.append(v.get("path"))
                        break

        def run():
            organizer = VideoOrganizer(self.settings)
            preview = organizer.run_rename(
                dry_run=dry_run, selected_paths=selected_paths if selected_paths else None)
            if dry_run and preview:
                self.after(0, self.show_rename_preview, preview)
            self.after(0, self.refresh_table)
            self.after(0, lambda: self.status_var.set("重命名操作完成"))

        threading.Thread(target=run, daemon=True).start()

    def show_rename_preview(self, preview):
        preview_win = ctk.CTkToplevel(self)
        preview_win.title("重命名预览")
        preview_win.geometry("700x500")

        txt = ctk.CTkTextbox(preview_win)
        txt.pack(fill="both", expand=True, padx=20, pady=20)

        for p in preview:
            txt.insert(tk.END, f"旧: {p['old']}\n新: {p['new']}\n{'-'*40}\n")

        ctk.CTkButton(preview_win, text="关闭",
                      command=preview_win.destroy).pack(pady=10)

    def undo_rename(self):
        if not os.path.exists(UNDO_FILE):
            messagebox.showinfo("信息", "找不到撤销脚本")
            return
        if messagebox.askyesno("确认", "确定撤销上次重命名吗？"):
            try:
                spec = importlib.util.spec_from_file_location(
                    "undo_rename", UNDO_FILE)
                if spec and spec.loader:
                    undo_module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(undo_module)
                    if hasattr(undo_module, 'undo'):
                        undo_module.undo()
                        self.refresh_table()
                        messagebox.showinfo("成功", "撤销成功")
            except Exception as e:
                messagebox.showerror("错误", f"撤销失败: {e}")

    # --- 标签管理 ---
    def refresh_tags_ui(self):
        # 分类
        self.cat_listbox.delete(0, tk.END)
        for c in self.settings.get("categories", []):
            self.cat_listbox.insert(tk.END, c)

        # 维度
        self.dim_listbox.delete(0, tk.END)
        for d in self.settings.get("tag_dimensions", {}).keys():
            self.dim_listbox.insert(tk.END, d)

        self.tag_val_listbox.delete(0, tk.END)

    def on_dim_select_ui(self, event):
        sel = self.dim_listbox.curselection()
        if not sel:
            return
        dim = self.dim_listbox.get(sel[0])
        self.tag_content_label.configure(text=f"维度 [{dim}] 的内容")
        self.tag_val_listbox.delete(0, tk.END)
        for t in self.settings.get("tag_dimensions", {}).get(dim, []):
            self.tag_val_listbox.insert(tk.END, t)

    def add_dimension_ui(self):
        from tkinter import simpledialog
        name = simpledialog.askstring("添加维度", "输入新维度名称:")
        if name:
            if name not in self.settings["tag_dimensions"]:
                self.settings["tag_dimensions"][name] = []
                self.refresh_tags_ui()

    def add_tag_ui(self, target_dim):
        if not target_dim:
            sel = self.dim_listbox.curselection()
            if not sel:
                messagebox.showwarning("警告", "请先选择一个维度")
                return
            target_dim = self.dim_listbox.get(sel[0])

        from tkinter import simpledialog
        name = simpledialog.askstring("添加标签", f"向 [{target_dim}] 添加标签:")
        if name:
            if target_dim == "Categories":
                if name not in self.settings["categories"]:
                    self.settings["categories"].append(name)
            else:
                if name not in self.settings["tag_dimensions"][target_dim]:
                    self.settings["tag_dimensions"][target_dim].append(name)
            self.refresh_tags_ui()

    def merge_tag_ui(self, target_dim):
        sel_dim = self.dim_listbox.curselection()
        sel_tag = self.tag_val_listbox.curselection()
        if not sel_dim or not sel_tag:
            messagebox.showwarning("警告", "请先选择要合并的标签")
            return

        dim = self.dim_listbox.get(sel_dim[0])
        old_tag = self.tag_val_listbox.get(sel_tag[0])

        from tkinter import simpledialog
        new_tag = simpledialog.askstring("合并标签", f"将标签 [{old_tag}] 合并到:")
        if not new_tag:
            return

        if messagebox.askyesno("确认", f"确定要将所有视频中的 '{old_tag}' 替换为 '{new_tag}' 吗？"):
            # 更新数据库中的所有视频
            videos = self.db.get_all_videos()
            count = 0
            for v in videos:
                if old_tag in v.get("tags", []):
                    new_tags = [new_tag if t ==
                                old_tag else t for t in v["tags"]]
                    v["tags"] = new_tags
                    self.db.upsert_video(v)
                    count += 1

            # 更新标签库
            if old_tag in self.settings["tag_dimensions"].get(dim, []):
                self.settings["tag_dimensions"][dim].remove(old_tag)
                if new_tag not in self.settings["tag_dimensions"][dim]:
                    self.settings["tag_dimensions"][dim].append(new_tag)

            self.refresh_tags_ui()
            self.refresh_table()
            messagebox.showinfo("成功", f"合并完成，共更新 {count} 个视频")

    def delete_tag_ui(self, target_dim):
        if not target_dim:
            sel_dim = self.dim_listbox.curselection()
            sel_tag = self.tag_val_listbox.curselection()
            if not sel_dim or not sel_tag:
                return
            target_dim = self.dim_listbox.get(sel_dim[0])
            tag_name = self.tag_val_listbox.get(sel_tag[0])
            self.settings["tag_dimensions"][target_dim].remove(tag_name)
        else:
            sel_tag = self.cat_listbox.curselection()
            if not sel_tag:
                return
            tag_name = self.cat_listbox.get(sel_tag[0])
            self.settings["categories"].remove(tag_name)
        self.refresh_tags_ui()

    # --- 设置保存 ---
    def load_settings_to_ui(self):
        # 已经在初始化和 frame 设置中通过变量绑定处理了部分
        # 这里的 refresh_table 会触发第一次数据加载
        self.refresh_table()


if __name__ == "__main__":
    app = VideoOrganizerGUI()
    app.mainloop()
