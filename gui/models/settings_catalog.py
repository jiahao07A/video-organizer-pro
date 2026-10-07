# -*- coding: utf-8 -*-
"""设置目录：面向用户任务的分类导航与可搜索索引。

本模块只描述「设置在哪、属于哪一类、改动何时生效」，
不读取配置值，也不写配置。设置键与 GUI 控件之间以 widget_key 解耦，
供 ``gui.views.settings`` 注册控件后做搜索定位。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

# 普通界面偏好：改动后防抖自动保存并显示状态。
# 敏感/路由/导出等配置：保留明确保存区。
AUTOSAVE = "autosave"
MANUAL = "manual"


@dataclass(frozen=True)
class SettingsCategory:
    """按用户任务划分的设置分类。"""

    id: str
    title: str
    description: str


CATEGORIES: Tuple[SettingsCategory, ...] = (
    SettingsCategory("everyday", "日常偏好", "主题、字体、视图与详情面板等界面呈现。"),
    SettingsCategory("analysis", "分析与处理", "并发、抽帧、重试与副作用开关。"),
    SettingsCategory("ai", "AI 服务与模型", "API Key、供应商档案与任务模型路由。"),
    SettingsCategory("prompts", "提示词", "分析角色头、标签组引导词与标签库 AI 模板。"),
    SettingsCategory("naming", "命名与导出", "重命名模板、XMP 与 ALE 导出列。"),
)


@dataclass(frozen=True)
class SettingsEntry:
    """目录中的一条设置项。"""

    key: str
    widget_key: str
    label: str
    category_id: str
    keywords: Tuple[str, ...]
    save_mode: str
    effect: str  # 改动影响什么 / 何时生效
    tab_index: int  # 所属页签（0 常规 / 1 AI / 2 提示词 / 3 界面 / 4 导出）


@dataclass(frozen=True)
class SettingsMatch:
    """搜索命中的设置项及其所属分类。"""

    entry: SettingsEntry
    category: SettingsCategory
    score: int


# 覆盖当前设置界面全部可编辑项；key 保持与现有配置键一致。
SETTINGS_ENTRIES: Tuple[SettingsEntry, ...] = (
    # —— 界面页签（日常偏好，自动保存）——
    SettingsEntry(
        "ui_preferences.theme", "theme", "主题",
        "everyday", ("主题", "theme", "深色", "浅色", "外观"),
        AUTOSAVE, "界面配色，保存后立即生效。", 3,
    ),
    SettingsEntry(
        "ui_preferences.font_size", "font_size", "全局字体大小",
        "everyday", ("字体", "字号", "font"),
        AUTOSAVE, "应用于全局界面文字，保存后立即生效。", 3,
    ),
    SettingsEntry(
        "ui_preferences.default_view", "default_view", "默认视图",
        "everyday", ("默认视图", "列表", "卡片", "视图"),
        AUTOSAVE, "新打开页面的默认列表/卡片视图，保存后立即生效。", 3,
    ),
    SettingsEntry(
        "ui_preferences.sidebar_width", "sidebar_width", "侧边栏宽度",
        "everyday", ("侧边栏", "宽度", "sidebar"),
        AUTOSAVE, "导航侧栏宽度，保存后立即生效。", 3,
    ),
    SettingsEntry(
        "ui_preferences.detail_panel_expanded", "detail_panel_expanded", "详情面板默认展开",
        "everyday", ("详情面板", "展开", "收起"),
        AUTOSAVE, "列表页详情面板的默认展开状态，保存后立即生效。", 3,
    ),
    SettingsEntry(
        "ui_preferences.remember_work_scope", "remember_work_scope", "记住上次工作范围",
        "everyday", ("工作范围", "记住", "启动恢复"),
        AUTOSAVE, "开启后启动时恢复上次工作范围路径。", 3,
    ),
    SettingsEntry(
        "ui_preferences.thumbnail_size", "thumbnail_size", "缩略图尺寸",
        "everyday", ("缩略图", "尺寸", "宽", "高", "thumbnail"),
        AUTOSAVE, "列表/卡片缩略图显示尺寸，保存后下次加载生效。", 3,
    ),
    # —— 常规页签 ——
    SettingsEntry(
        "processing.max_workers", "workers", "分析管线并发",
        "analysis", ("并发", "管线", "max_workers", "线程"),
        MANUAL, "分析任务的并发上限，保存后用于下一轮分析。", 0,
    ),
    SettingsEntry(
        "processing.extract_parallel", "extract_parallel", "同时抽帧路数",
        "analysis", ("抽帧", "解码", "openCV", "extract_parallel"),
        MANUAL, "同时 OpenCV 解码抽帧的上限，保存后用于下一轮分析。", 0,
    ),
    SettingsEntry(
        "processing.extract_workers", "extract_workers", "入库抽帧并发",
        "analysis", ("入库", "缩略图", "抽帧", "extract_workers"),
        MANUAL, "工作范围入库时缩略图抽帧并发，保存后用于下一次入库。", 0,
    ),
    SettingsEntry(
        "processing.max_frames", "frames", "AI 分析抽帧数",
        "analysis", ("抽帧数", "帧", "max_frames"),
        MANUAL, "每个视频送给 AI 的帧数，保存后用于下一轮分析。", 0,
    ),
    SettingsEntry(
        "processing.jpeg_quality", "quality", "JPEG 压缩质量",
        "analysis", ("jpeg", "压缩", "质量"),
        MANUAL, "抽帧图片压缩质量，保存后用于下一轮分析。", 0,
    ),
    SettingsEntry(
        "processing.enable_scene_detection", "scene_detect", "智能场景检测",
        "analysis", ("场景检测", "scene"),
        MANUAL, "是否启用场景检测，保存后用于下一轮分析。", 0,
    ),
    SettingsEntry(
        "processing.enable_audio_transcription", "audio", "音频转录",
        "analysis", ("音频", "转录", "whisper"),
        MANUAL, "是否启用音频转录（需 ffmpeg），保存后用于下一轮分析。", 0,
    ),
    SettingsEntry(
        "processing.auto_sync_xmp_after_analysis", "auto_xmp", "分析后自动同步 XMP",
        "analysis", ("xmp", "侧车", "自动同步", "副作用"),
        MANUAL, "开启后分析成功即写 xmp 侧车，保存后对下一轮分析生效。", 0,
    ),
    SettingsEntry(
        "processing.analysis_retry.call_extra_attempts", "call_extra", "调用层额外重试",
        "analysis", ("重试", "retry"),
        MANUAL, "单次 AI 请求的额外重试次数，保存后用于下一轮分析。", 0,
    ),
    SettingsEntry(
        "processing.analysis_retry.item_max_attempts", "item_max", "单条最大尝试",
        "analysis", ("重试", "尝试", "item"),
        MANUAL, "单条视频完整流程最大尝试次数，保存后用于下一轮分析。", 0,
    ),
    SettingsEntry(
        "processing.analysis_retry.batch_rerun_enabled", "batch_rerun", "自动补跑失败项",
        "analysis", ("补跑", "失败", "batch"),
        MANUAL, "首轮结束后是否自动补跑失败项，保存后用于下一轮分析。", 0,
    ),
    # —— AI 页签（敏感 / 路由，明确保存区）——
    SettingsEntry(
        "api.key", "api_key", "API Key",
        "ai", ("key", "密钥", "api", "供应商"),
        MANUAL, "当前供应商的 Key；保存后重建 AI 客户端对新请求生效。", 1,
    ),
    SettingsEntry(
        "api.base_url", "api_url", "API Base URL",
        "ai", ("base", "url", "地址", "接口"),
        MANUAL, "当前供应商的服务地址；保存后重建 AI 客户端对新请求生效。", 1,
    ),
    SettingsEntry(
        "model_providers", "provider_list", "模型供应商",
        "ai", ("供应商", "档案", "provider"),
        MANUAL, "最多五个连接档案，保存后落盘。", 1,
    ),
    SettingsEntry(
        "task_model_routing", "task_routing", "任务模型路由",
        "ai", ("路由", "模型", "routing", "分类", "标签", "描述"),
        MANUAL, "为每个 AI 功能指定供应商与模型，保存后对新请求生效。", 1,
    ),
    # —— 提示词页签（明确保存区）——
    SettingsEntry(
        "tag_config.global_settings.system_prompt", "system_prompt", "全局角色头",
        "prompts", ("角色头", "system", "prompt", "分析"),
        MANUAL, "分析使用的全局角色提示，保存后用于下一轮分析。", 2,
    ),
    SettingsEntry(
        "tag_config.tag_groups.rules.local_prompt", "local_prompt", "标签组引导词",
        "prompts", ("局部", "local", "prompt", "标签组"),
        MANUAL, "各标签组的局部引导词，保存后用于下一轮分析。", 2,
    ),
    SettingsEntry(
        "tag_ai_prompts", "tag_ai_prompts", "标签库 AI 模板",
        "prompts", ("标签库", "ai", "模板", "待审", "近义", "标准词"),
        MANUAL, "标签库 AI 三类模板，保存后用于后续 AI 建议。", 2,
    ),
    # —— 导出页签（明确保存区）——
    SettingsEntry(
        "rename_pattern", "rename_pattern", "重命名模式",
        "naming", ("重命名", "命名", "模式", "rename", "pattern", "模板"),
        MANUAL, "自动重命名使用的文件名模板，保存后用于下一次重命名。", 4,
    ),
    SettingsEntry(
        "global_settings.language", "xmp_lang", "XMP 导出语言",
        "naming", ("xmp", "语言", "language"),
        MANUAL, "XMP 侧车导出语言，保存后用于下一次导出。", 4,
    ),
    SettingsEntry(
        "global_settings.export_schemes.xmp_hierarchical", "xmp_hierarchical", "层级标签",
        "naming", ("层级", "标签", "xmp", "hierarchical"),
        MANUAL, "XMP 层级标签（Parent|Child）开关，保存后用于下一次导出。", 4,
    ),
    SettingsEntry(
        "global_settings.export_schemes.xmp_prefix_category", "xmp_prefix_category", "分类前缀",
        "naming", ("前缀", "分类", "xmp", "prefix"),
        MANUAL, "标签前是否加分类前缀，保存后用于下一次导出。", 4,
    ),
    SettingsEntry(
        "global_settings.export_schemes.ale_columns", "ale_columns", "ALE 导出列",
        "naming", ("ale", "列", "列定义", "达芬奇"),
        MANUAL, "ALE 导出字段列表，保存后用于下一次导出。", 4,
    ),
)


_CATEGORY_BY_ID: Dict[str, SettingsCategory] = {c.id: c for c in CATEGORIES}
_ENTRY_BY_KEY: Dict[str, SettingsEntry] = {e.key: e for e in SETTINGS_ENTRIES}
_ENTRY_BY_WIDGET: Dict[str, SettingsEntry] = {e.widget_key: e for e in SETTINGS_ENTRIES}


def list_categories() -> Tuple[SettingsCategory, ...]:
    """返回按用户任务排序的全部分类。"""
    return CATEGORIES


def get_category(category_id: str) -> SettingsCategory | None:
    return _CATEGORY_BY_ID.get(category_id)


def list_entries_in(category_id: str) -> List[SettingsEntry]:
    return [e for e in SETTINGS_ENTRIES if e.category_id == category_id]


def get_entry_by_key(key: str) -> SettingsEntry | None:
    return _ENTRY_BY_KEY.get(key)


def get_entry_by_widget(widget_key: str) -> SettingsEntry | None:
    return _ENTRY_BY_WIDGET.get(widget_key)


def _score_entry(entry: SettingsEntry, query: str) -> int:
    """命中打分：标签 > 关键词前缀 > 关键词包含 > 键名包含；未命中 0。"""
    label = entry.label.lower()
    if query in label:
        return 4 if label.startswith(query) else 3
    best = 0
    for kw in entry.keywords:
        kw_l = str(kw).lower()
        if query in kw_l:
            best = max(best, 2)
    if query in entry.key.lower():
        best = max(best, 1)
    return best


def search_entries(query: str) -> List[SettingsMatch]:
    """按用户输入返回命中设置项，按相关度排序。

    空查询返回全部条目（按分类与目录顺序）。
    """
    q = (query or "").strip().lower()
    matches: List[SettingsMatch] = []
    if not q:
        for order, entry in enumerate(SETTINGS_ENTRIES):
            cat = _CATEGORY_BY_ID.get(entry.category_id)
            if cat is None:
                continue
            matches.append(SettingsMatch(entry=entry, category=cat, score=0))
        return matches

    for entry in SETTINGS_ENTRIES:
        cat = _CATEGORY_BY_ID.get(entry.category_id)
        if cat is None:
            continue
        score = _score_entry(entry, q)
        if score > 0:
            # 让分类标题命中也能被搜到
            if q in cat.title.lower():
                score = max(score, 2)
            matches.append(SettingsMatch(entry=entry, category=cat, score=score))
    matches.sort(key=lambda m: (-m.score, m.category.id, m.entry.label))
    return matches
