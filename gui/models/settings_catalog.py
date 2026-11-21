# -*- coding: utf-8 -*-
"""设置目录：面向用户任务的分类导航与可搜索索引。

本模块只描述「设置在哪、属于哪一类、改动何时生效」，
不读取配置值，也不写配置。设置键与 GUI 控件之间以 widget_key 解耦，
供 ``gui.views.settings`` 注册控件后做搜索定位。
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Dict, List, Tuple
from core.watch_config import DEFAULTS as _WATCH_DEFAULTS

# 所有设置统一防抖自动保存并显示状态；MANUAL 保留为兼容旧调用方的别名。
AUTOSAVE = "autosave"
MANUAL = AUTOSAVE


@dataclass(frozen=True)
class SettingsCategory:
    """按用户任务划分的设置分类。"""

    id: str
    title: str
    description: str
    group: str = ""


CATEGORIES: Tuple[SettingsCategory, ...] = (
    SettingsCategory("everyday", "界面与工作区", "外观、默认视图、素材列表和工作范围。", "工作区"),
    SettingsCategory("ai_connections", "AI 连接", "管理 API 服务地址、密钥和默认连接。", "AI 工作流"),
    SettingsCategory("ai_models", "AI 模型分配", "为分类、标签和助手任务选择模型。", "AI 工作流"),
    SettingsCategory("prompts", "提示词与标签", "管理分析提示、标签组引导词和标签 AI 模板。", "AI 工作流"),
    SettingsCategory("video_analysis", "视频分析", "控制分析并发、抽帧、画面和音频准备。", "素材处理"),
    SettingsCategory("failure_handling", "失败处理", "管理普通重试和 HTTP 400 专用恢复策略。", "素材处理"),
    SettingsCategory("monitoring", "监控默认值", "未覆盖的监控任务持续继承这些默认规则。", "素材处理"),
    SettingsCategory("naming", "命名与导出", "管理文件名模板、XMP 和 ALE 导出格式。", "输出与维护"),
    SettingsCategory("system", "数据与维护", "管理数据目录、备份和软件更新。", "输出与维护"),
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
    tab_index: int
    page_id: str = ""
    card_id: str = ""
    storage_target: str = "settings"
    scope: str = "global"
    advanced: bool = False
    reset_group: str = ""
    default_value: object = None
    effective_when: str = "后续相关操作生效"
    config_path: str = ""


@dataclass(frozen=True)
class SettingsMatch:
    """搜索命中的设置项及其所属分类。"""

    entry: SettingsEntry
    category: SettingsCategory
    score: int


# 覆盖当前设置界面全部可编辑项；key 保持与现有配置键一致。
SETTINGS_ENTRIES: Tuple[SettingsEntry, ...] = (
    SettingsEntry(
        "software_update", "software_update", "软件更新",
        "system", ("更新", "版本", "检查更新", "测试版", "离线", "下载"),
        MANUAL, "自动检查、渠道和离线安装；独立即时保存。", 5,
    ),
    # —— 界面页签（日常偏好，自动保存）——
    SettingsEntry(
        "ui_preferences.theme", "theme", "主题",
        "everyday", ("主题", "theme", "深色", "浅色", "外观"),
        AUTOSAVE, "界面配色，保存后立即生效。", 0,
    ),
    SettingsEntry(
        "ui_preferences.font_size", "font_size", "全局字体大小",
        "everyday", ("字体", "字号", "font"),
        AUTOSAVE, "应用于全局界面文字，保存后立即生效。", 0,
    ),
    SettingsEntry(
        "ui_preferences.default_view", "default_view", "默认视图",
        "everyday", ("默认视图", "列表", "卡片", "视图"),
        AUTOSAVE, "新打开页面的默认列表/卡片视图，保存后立即生效。", 0,
    ),
    SettingsEntry(
        "ui_preferences.sidebar_width", "sidebar_width", "侧边栏宽度",
        "everyday", ("侧边栏", "宽度", "sidebar"),
        AUTOSAVE, "导航侧栏宽度，保存后立即生效。", 0,
    ),
    SettingsEntry(
        "ui_preferences.detail_panel_expanded", "detail_panel_expanded", "详情面板默认展开",
        "everyday", ("详情面板", "展开", "收起"),
        AUTOSAVE, "列表页详情面板的默认展开状态，保存后立即生效。", 0,
    ),
    SettingsEntry(
        "ui_preferences.remember_work_scope", "remember_work_scope", "记住上次工作范围",
        "everyday", ("工作范围", "记住", "启动恢复"),
        AUTOSAVE, "开启后启动时恢复上次工作范围路径。", 0,
    ),
    SettingsEntry(
        "ui_preferences.thumbnail_size", "thumbnail_size", "缩略图尺寸",
        "everyday", ("缩略图", "尺寸", "宽", "高", "thumbnail"),
        AUTOSAVE, "列表/卡片缩略图显示尺寸，保存后下次加载生效。", 0,
    ),
    # —— 常规页签 ——
    SettingsEntry(
        "processing.max_workers", "workers", "同时分析的视频数",
        "analysis", ("并发", "管线", "max_workers", "线程", "分析管线并发"),
        MANUAL, "同时处理视频的上限（包括图片准备和等待AI），不是每秒请求数；保存后用于下一轮分析。", 1,
    ),
    SettingsEntry(
        "processing.extract_parallel", "extract_parallel", "分析图片准备并行数",
        "analysis", ("抽帧", "解码", "openCV", "extract_parallel", "同时抽帧路数"),
        MANUAL, "为AI准备图片的并行视频数，默认4；图片就绪后才请求AI。保存后用于下一轮分析。", 1,
    ),
    SettingsEntry(
        "processing.extract_workers", "extract_workers", "入库缩略图并行数",
        "analysis", ("入库", "缩略图", "抽帧", "extract_workers", "入库抽帧并发"),
        MANUAL, "仅控制新素材入库的缩略图生成，不影响分析或标签AI请求。保存后用于下一次入库。", 1,
    ),
    SettingsEntry(
        "processing.max_frames", "frames", "AI 分析抽帧数",
        "analysis", ("抽帧数", "帧", "max_frames"),
        MANUAL, "每个视频送给 AI 的帧数，保存后用于下一轮分析。", 1,
    ),
    SettingsEntry(
        "processing.jpeg_quality", "quality", "JPEG 压缩质量",
        "analysis", ("jpeg", "压缩", "质量"),
        MANUAL, "抽帧图片压缩质量，保存后用于下一轮分析。", 1,
    ),
    SettingsEntry(
        "processing.enable_scene_detection", "scene_detect", "智能场景检测",
        "analysis", ("场景检测", "scene"),
        MANUAL, "场景检测需遍历解码视频；关闭后均匀抽帧，图片选择会不同。保存后用于下一轮分析。", 1,
    ),
    SettingsEntry(
        "processing.enable_audio_transcription", "audio", "音频转录",
        "analysis", ("音频", "转录", "whisper"),
        MANUAL, "是否启用音频转录（需 ffmpeg），保存后用于下一轮分析。", 1,
    ),
    SettingsEntry(
        "processing.auto_sync_xmp_after_analysis", "auto_xmp", "分析后自动同步 XMP",
        "analysis", ("xmp", "侧车", "自动同步", "副作用"),
        MANUAL, "开启后分析成功即写 xmp 侧车，保存后对下一轮分析生效。", 1,
    ),
    SettingsEntry(
        "processing.analysis_retry.call_extra_attempts", "call_extra", "调用层额外重试",
        "analysis", ("重试", "retry"),
        MANUAL, "单次 AI 请求的额外重试次数，保存后用于下一轮分析。", 1,
    ),
    SettingsEntry(
        "processing.analysis_retry.item_max_attempts", "item_max", "单条最大尝试",
        "analysis", ("重试", "尝试", "item"),
        MANUAL, "单条视频完整流程最大尝试次数，保存后用于下一轮分析。", 1,
    ),
    SettingsEntry(
        "processing.analysis_retry.batch_rerun_enabled", "batch_rerun", "自动补跑失败项",
        "analysis", ("补跑", "失败", "batch"),
        MANUAL, "首轮结束后是否自动补跑失败项，保存后用于下一轮分析。", 1,
    ),
    SettingsEntry(
        "processing.http400_recovery", "http400_recovery", "HTTP 400 重试与备用 API",
        "analysis", ("400", "兜底", "备用", "重试", "fallback", "恢复"),
        MANUAL, "在素材与处理页的分析失败处理区域编辑，自动保存后对下一轮分析生效。", 1,
    ),
    # —— AI 服务（敏感 / 路由，自动保存）——
    SettingsEntry(
        "api.key", "api_key", "API Key",
        "ai", ("key", "密钥", "api", "供应商"),
        MANUAL, "当前供应商的 Key；保存后重建 AI 客户端对新请求生效。", 2,
    ),
    SettingsEntry(
        "api.base_url", "api_url", "API Base URL",
        "ai", ("base", "url", "地址", "接口"),
        MANUAL, "当前供应商的服务地址；保存后重建 AI 客户端对新请求生效。", 2,
    ),
    SettingsEntry(
        "model_providers", "provider_list", "模型供应商",
        "ai", ("供应商", "档案", "provider"),
        MANUAL, "最多五个连接档案，保存后落盘。", 2,
    ),
    SettingsEntry(
        "model_provider.display_name", "provider_name", "供应商显示名称",
        "ai", ("供应商", "名称", "档案", "display_name"),
        AUTOSAVE, "当前连接的显示名称，保存后立即更新档案。", 2,
    ),
    SettingsEntry(
        "model_provider.video_classification", "provider_default_classification", "视频分类默认模型",
        "ai", ("供应商", "默认模型", "视频分类", "classification"),
        AUTOSAVE, "当前连接的视频分类默认模型，未单独路由时作为回退。", 2,
    ),
    SettingsEntry(
        "model_provider.tag_generation", "provider_default_tags", "标签生成默认模型",
        "ai", ("供应商", "默认模型", "标签", "tag"),
        AUTOSAVE, "当前连接的标签生成默认模型，未单独路由时作为回退。", 2,
    ),
    SettingsEntry(
        "model_provider.content_description", "provider_default_description", "内容描述默认模型",
        "ai", ("供应商", "默认模型", "描述", "description"),
        AUTOSAVE, "当前连接的内容描述默认模型，未单独路由时作为回退。", 2,
    ),
    SettingsEntry(
        "task_model_routing", "task_routing", "任务模型路由",
        "ai", ("路由", "模型", "routing", "分类", "标签", "描述"),
        MANUAL, "为每个 AI 功能指定供应商与模型，保存后对新请求生效。", 2,
    ),
    # —— 提示词与标签（自动保存）——
    SettingsEntry(
        "tag_config.global_settings.system_prompt", "system_prompt", "全局角色头",
        "prompts", ("角色头", "system", "prompt", "分析"),
        MANUAL, "分析使用的全局角色提示，保存后用于下一轮分析。", 3,
    ),
    SettingsEntry(
        "tag_config.tag_groups.rules.local_prompt", "local_prompt", "标签组引导词",
        "prompts", ("局部", "局部提示词", "local", "prompt", "标签组"),
        MANUAL, "各标签组的局部引导词，保存后用于下一轮分析。", 3,
    ),
    SettingsEntry(
        "tag_ai_prompts", "tag_ai_prompts", "标签库 AI 模板",
        "prompts", ("标签库", "ai", "模板", "待审", "近义", "标准词"),
        MANUAL, "标签库 AI 三类模板，保存后用于后续 AI 建议。", 3,
    ),
    # —— 命名与导出（自动保存）——
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

_PAGE_BY_WIDGET = {
    "theme": "everyday", "font_size": "everyday", "default_view": "everyday",
    "sidebar_width": "everyday", "detail_panel_expanded": "everyday",
    "remember_work_scope": "everyday", "thumbnail_size": "everyday",
    "software_update": "system",
    "workers": "video_analysis", "extract_parallel": "video_analysis",
    "extract_workers": "video_analysis", "frames": "video_analysis",
    "quality": "video_analysis", "scene_detect": "video_analysis",
    "audio": "video_analysis", "auto_xmp": "naming",
    "call_extra": "failure_handling", "item_max": "failure_handling",
    "batch_rerun": "failure_handling", "http400_recovery": "failure_handling",
    "api_key": "ai_connections", "api_url": "ai_connections",
    "provider_list": "ai_connections", "provider_name": "ai_connections",
    "provider_default_classification": "ai_models", "provider_default_tags": "ai_models",
    "provider_default_description": "ai_models", "task_routing": "ai_models",
    "system_prompt": "prompts", "local_prompt": "prompts", "tag_ai_prompts": "prompts",
    "rename_pattern": "naming", "xmp_lang": "naming",
    "xmp_hierarchical": "naming", "xmp_prefix_category": "naming", "ale_columns": "naming",
}
_CONNECTION_WIDGETS = {"api_key", "api_url", "provider_list", "provider_name"}
_MODEL_WIDGETS = {"provider_default_classification", "provider_default_tags", "provider_default_description", "task_routing"}
_FAILURE_WIDGETS = {"call_extra", "item_max", "batch_rerun", "http400_recovery"}
_RENAME_WIDGETS = {"rename_pattern", "xmp_lang", "xmp_hierarchical", "xmp_prefix_category", "ale_columns", "auto_xmp"}
_ADVANCED_WIDGETS = {"sidebar_width", "thumbnail_size", "extract_workers", "quality", "call_extra", "item_max", "http400_recovery", "local_prompt"}
_DEFAULTS_BY_WIDGET = {
    "theme": "dark", "font_size": 14, "default_view": "list", "sidebar_width": 220,
    "detail_panel_expanded": True, "remember_work_scope": False, "thumbnail_size": [160, 90],
    "workers": 16, "extract_parallel": 4, "extract_workers": 8, "frames": 10,
    "quality": 80, "scene_detect": True, "audio": False, "auto_xmp": False,
    "call_extra": 2, "item_max": 2, "batch_rerun": True,
    "rename_pattern": "{category}-{tags}-{summary}-{original_name}",
    "xmp_lang": "zh-CN", "xmp_hierarchical": True, "xmp_prefix_category": True,
    "ale_columns": ["Name", "Keywords", "Category", "Summary"],
}
def _decorate_entry(entry: SettingsEntry) -> SettingsEntry:
    widget = entry.widget_key
    page = _PAGE_BY_WIDGET.get(widget, entry.category_id)
    if widget in _CONNECTION_WIDGETS:
        card, reset = "provider_connections", ""
    elif widget in _MODEL_WIDGETS:
        card = "task_model_routing" if widget == "task_routing" else "provider_model_defaults"
        reset = card
    elif widget in _FAILURE_WIDGETS:
        card, reset = ("http400_recovery" if widget == "http400_recovery" else "ordinary_retries"), "http400_recovery" if widget == "http400_recovery" else "ordinary_retries"
    elif widget in _RENAME_WIDGETS:
        card = "rename_template" if widget == "rename_pattern" else "xmp_export" if widget in {"auto_xmp", "xmp_lang", "xmp_hierarchical", "xmp_prefix_category"} else "ale_export"
        reset = card
    elif page == "everyday":
        card = {"theme": "appearance", "font_size": "appearance",
                "remember_work_scope": "work_scope", "sidebar_width": "workspace_layout",
                "thumbnail_size": "material_display", "default_view": "material_display",
                "detail_panel_expanded": "workspace_layout"}[widget]
        reset = card
    elif page == "video_analysis":
        card = {"workers": "analysis_pipeline", "extract_parallel": "analysis_preparation",
                "extract_workers": "ingestion", "frames": "analysis_preparation",
                "quality": "analysis_preparation", "scene_detect": "analysis_preparation",
                "audio": "audio_transcription"}[widget]
        reset = card
    elif widget == "tag_ai_prompts":
        card, reset = "tag_ai_prompts", "tag_ai_prompts"
    else:
        card, reset = page, page
    storage = "tag_config" if entry.key.startswith("tag_config.") or entry.key.startswith("global_settings.") else "settings"
    if widget == "local_prompt":
        storage, page, card, reset = "tag_config", "prompts", "group_prompts", "group_prompts"
    if widget == "system_prompt":
        storage, page, card, reset = "tag_config", "prompts", "analysis_prompt", "analysis_prompt"
    scope = "provider_profile" if widget in _CONNECTION_WIDGETS else "ai_task" if widget in _MODEL_WIDGETS else "global"
    return replace(entry, category_id=page, page_id=page, card_id=card, storage_target=storage,
                   effective_when="下次启动生效" if widget == "remember_work_scope" else "下次加载生效" if widget == "thumbnail_size" else "下次入库生效" if widget == "extract_workers" else "立即生效" if page == "everyday" else "后续 AI 请求生效" if page.startswith("ai_") or page == "prompts" else "下次处理生效",
                   scope=scope, advanced=widget in _ADVANCED_WIDGETS, reset_group=reset,
                   config_path=("model_providers[selected]." + {"api_key": "api_key", "api_url": "base_url", "provider_name": "display_name"}.get(widget, "models." + entry.key.rsplit(".", 1)[-1])) if widget in _CONNECTION_WIDGETS | _MODEL_WIDGETS and widget not in {"provider_list", "task_routing"} else entry.key,
                   default_value=_DEFAULTS_BY_WIDGET.get(widget),
                   tab_index=next(i for i,c in enumerate(CATEGORIES) if c.id == page))

SETTINGS_ENTRIES = tuple(map(_decorate_entry, SETTINGS_ENTRIES)) + (
    SettingsEntry("current_provider_id", "default_connection", "默认回退连接", "ai_connections",
                  ("默认", "回退", "供应商"), AUTOSAVE, "未指定连接的任务使用此连接；后续请求生效。", 1,
                  page_id="ai_connections", card_id="provider_connections", scope="global", config_path="current_provider_id", effective_when="后续 AI 请求生效"),
    SettingsEntry("data_directory", "data_directory", "数据目录与备份", "system",
                  ("数据", "目录", "备份", "恢复", "搬迁"), AUTOSAVE, "必须手动触发；切换数据目录需要重启。", 8,
                  page_id="system", card_id="data_maintenance", scope="workspace", config_path="data_directory_locator", effective_when="需要重启"),
)
_WATCH_LABELS = {
    "start_with_windows": "Windows 登录时启动", "initial_scan_mode": "首次扫描默认范围",
    "ask_initial_scan": "添加目录时询问首次扫描范围", "stability_seconds": "等待文件写入稳定（秒）",
    "check_interval": "稳定性检查间隔（秒）", "rescan_interval": "周期补扫间隔（秒）",
    "batch_size": "每批最多素材数", "max_workers": "后台分析并发上限（0＝跟随分析设置）",
    "request_interval": "请求间隔（秒，-1＝跟随分析设置）", "call_extra_attempts": "调用额外重试次数（-1＝跟随分析设置）",
    "item_max_attempts": "单条最多尝试（0＝跟随分析设置）", "batch_rerun_max_rounds": "批次补跑轮数（-1＝跟随分析设置）",
    "rename_preset": "命名方案（留空继承命名设置）",
    "extensions": "视频扩展名", "include_patterns": "包含文件规则", "exclude_patterns": "排除文件规则",
    "excluded_dirs": "排除子目录（相对路径）", "rename_pattern": "自定义命名模板（留空跟随现有方案）",
    "regex_find": "文件名正则查找", "regex_replace": "正则替换为", "rename_conflict": "自动重命名遇到冲突",
    "xmp_mode": "分析后的 XMP 侧车", "notify_complete": "批次完成时通知", "notify_failure": "批次失败时通知",
    "recursive": "递归扫描子目录", "auto_analyze": "发现后自动分析", "auto_rename": "分析后自动重命名",
}
WATCH_ENTRIES = tuple(
    replace(SettingsEntry(
        f"processing.media_watch.{key}", f"watch_{key}", label, "monitoring",
        ("监控", "目录", "扫描", key), AUTOSAVE,
        "控制所有未覆盖此项的监控任务；目录专属设置在对应任务内编辑，下次监控批次生效。", 6,
        page_id="monitoring", card_id="monitor_defaults", storage_target="settings", config_path=f"processing.media_watch.{key}",
        scope="global_default", advanced=key not in {"recursive", "auto_analyze", "auto_rename", "start_with_windows"},
        reset_group="monitor_defaults", default_value=_WATCH_DEFAULTS[key],
    ))
    for key, label in _WATCH_LABELS.items()
)
SETTINGS_ENTRIES = SETTINGS_ENTRIES + WATCH_ENTRIES


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


def search_entries(query: str, entries=None) -> List[SettingsMatch]:
    """按用户输入返回命中设置项，按相关度排序。

    空查询返回全部条目（按分类与目录顺序）。
    """
    q = (query or "").strip().lower()
    entries = SETTINGS_ENTRIES if entries is None else entries
    matches: List[SettingsMatch] = []
    if not q:
        for order, entry in enumerate(entries):
            cat = _CATEGORY_BY_ID.get(entry.category_id)
            if cat is None:
                continue
            matches.append(SettingsMatch(entry=entry, category=cat, score=0))
        return matches

    for entry in entries:
        cat = _CATEGORY_BY_ID.get(entry.category_id)
        if cat is None:
            continue
        terms = q.split()
        scores = [_score_entry(entry, term) or (2 if term in cat.title.lower() else 0) for term in terms]
        score = sum(scores) if all(scores) else 0
        if score > 0:
            # 让分类标题命中也能被搜到
            if q in cat.title.lower():
                score = max(score, 2)
            matches.append(SettingsMatch(entry=entry, category=cat, score=score))
    matches.sort(key=lambda m: (-m.score, m.category.id, m.entry.label))
    return matches
