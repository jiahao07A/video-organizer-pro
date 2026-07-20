# 标签系统超级大更新 (MEGA UPDATE) 技术设计方案 v6.0

## 1. 核心概述
本次更新旨在将现有的半硬编码标签系统重构为高度动态、可自定义且由 AI 深度驱动的专业素材管理引擎。

## 2. 数据结构模型 (`tag_config.json` v6.0)

```json
{
  "version": "6.0",
  "global_settings": {
    "system_prompt": "你是一个资深的影视后期素材整理专家。请通过观察视频帧，提取精准的元数据。",
    "language": "zh-CN",
    "output_format": "json",
    "export_schemes": {
      "filename_pattern": "{category}-{tags}-{summary}",
      "xmp_hierarchical": true,
      "xmp_prefix_category": true,
      "ale_columns": ["Name", "Keywords", "Category", "Summary", "Emotion"]
    }
  },
  "categories": [
    { "id": "aroll", "display_name": "A-Roll (访谈/主体)", "order": 0 },
    { "id": "broll", "display_name": "B-Roll (空镜/素材)", "order": 1 },
    { "id": "working", "display_name": "工作记录", "order": 2 }
  ],
  "tag_groups": [
    {
      "id": "mood",
      "name": "氛围 (C1)",
      "rules": {
        "selection_mode": "single",
        "max_count": 1,
        "ai_expandable": false,
        "local_prompt": "专注于画面的视觉调性、光影感和情感色彩。"
      },
      "tags": ["温暖", "清冷", "赛博朋克", "极简", "胶片感"]
    },
    {
      "id": "subject",
      "name": "主体 (C2)",
      "rules": {
        "selection_mode": "multiple",
        "max_count": 3,
        "ai_expandable": true,
        "local_prompt": "识别视频中的核心人物、职业或关键物体。"
      },
      "tags": ["摄影师", "职场女性", "笔记本电脑"]
    }
  ]
}
```

## 3. 重构的 GUI 组件

### 3.1 动态标签库视图 (`TagsLibraryV2`)
- **动态分栏 (Dynamic Columns)**：不再使用硬编码的 6 分栏。根据 `tag_groups` 数组动态创建 `TagColumnWidget`。
- **分类管理器 (Category Manager)**：左侧新增一个可折叠的列表，用于管理大类。
- **规则配置悬浮窗**：点击组标题的齿轮图标，弹出气泡窗口修改 `rules`。

### 3.2 自定义 Prompt 管理中心
- **双窗格编辑器**：
  - 左侧：全局 System Prompt 编辑（带语法高亮，支持 `{categories}` 等占位符）。
  - 右侧：针对每个标签组的局部 Prompt 预览与编辑。
- **实时模拟器**：根据当前配置，预览发送给 AI 的完整文本。

### 3.3 深度 CRUD 交互
- **顺序调整**：实现分类和标签组的拖拽重排序。
- **快速合并**：选中多个标签，右键“一键合并”，自动更新数据库关联。

## 4. AI 提示词生成逻辑

### 4.1 动态构建算法
1.  **System Header**: 插入 `global_settings.system_prompt`。
2.  **Constraint Block**: 遍历 `tag_groups`，为每个组生成约束描述：
    - "对于组 '{name}'，必须遵循规则：{rules.selection_mode}，最大数量 {rules.max_count}。"
    - 插入 `rules.local_prompt`。
    - 列表化现有标签库供 AI 选择。
3.  **JSON Schema**: 动态生成要求的返回结构，包含所有 `tag_group.id` 作为 Key。

### 4.2 校验器 (Post-Processor)
- 强制执行 `max_count` 截断。
- 若 `ai_expandable` 为 false，则使用模糊匹配将 AI 的输出映射回现有标签库。

## 5. 20 个新功能与优化设计

| # | 功能名称 | 实现简述 |
|---|---|---|
| 1 | **层级标签支持 (Hierarchical Tags)** | 在 UI 中支持“父/子”标签展示，XMP 导出为 `Parent|Child` 格式。 |
| 2 | **同义词映射表** | 建立 `synonyms.json`，如“电脑”自动映射为“科技/电子产品”。 |
| 3 | **标签使用频率热力图** | 在标签库中以颜色深浅展示标签使用频次。 |
| 4 | **AI 标签推荐** | 选中一个素材时，基于已选标签，由向量数据库推荐相关标签。 |
| 5 | **批量正则表达式改名预览** | 支持 `{regex:pattern}` 占位符，在界面上实时显示重命名后的文件名。 |
| 6 | **多版本配置切换** | 支持保存不同的 `tag_config.json` 方案（如“婚礼方案”、“纪录片方案”）。 |
| 7 | **深色/浅色模式适配** | 基于 QSS 的主题切换引擎。 |
| 8 | **一键备份与恢复** | 将 `.db` 和 `.json` 压缩打包，支持快照回滚。 |
| 9 | **标签组互斥检查** | 标记某些标签组为互斥，防止 AI 同时给出一个视频“悲伤”和“狂喜”。 |
| 10 | **智能素材库迁移** | 根据标签自动将文件移动到目录：`分类/标签A/标签B/文件名`。 |
| 11 | **离线模型接入 (Ollama/Local)** | 允许用户指定本地 OpenAI 兼容接口地址。 |
| 12 | **自定义快捷键系统** | 允许为常用标签分配快捷键（如 1-9 键）。 |
| 13 | **人脸识别自动打标** | 集成面部识别模块，自动识别熟人并添加名字标签。 |
| 14 | **视频故事板生成** | 导出 PDF 报告，每个素材包含 3 张关键帧缩略图、标签和摘要。 |
| 15 | **标签搜索逻辑操作符** | 搜索框支持 `tag1 + tag2` (AND), `tag1 | tag2` (OR), `-tag3` (NOT)。 |
| 16 | **ALE 字段深度自定义** | 允许将任意标签组映射到 ALE 导出文件的特定列。 |
| 17 | **素材健康度仪表盘** | 统计未打标、只有单个标签、或 AI 置信度低的素材。 |
| 18 | **多语言标签转换** | 支持一键将中文标签库翻译为英文，方便国际合作。 |
| 19 | **自动生成颜色标签 (Color Labels)** | 根据主导色标签（如“日落”）自动应用对应的资源管理器颜色标记。 |
| 20 | **Git 驱动的配置版本控制** | 若工作目录有 Git，自动提交配置更改以便追溯。 |

## 6. 实施路线图
1.  **Phase 1**: 重构 `tag_config.json` 读取与 AI Prompt 拼接逻辑。
2.  **Phase 2**: 实现 `TagsLibraryV2` 动态列 UI。
3.  **Phase 3**: 开发 20 个优化功能中的核心功能（如层级标签、正则改名）。
