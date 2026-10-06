# 设置中心重设计：可搜索目录、任务分类导航与分级保存

Status: resolved
Created: 2026-10-05
Feature-slug: `gui-foundation`
Parent spec: `.scratch/gui-foundation/spec.md`（「设置界面」目标）

## 范围

在 `gui/views/settings.py` 落地 spec 中「设置界面」一节，不触碰素材查询 / 标签库文件，
不改变配置键、`SettingsManager.save_settings`、模型供应商规则与领域语义。

1. 可搜索设置目录 + 按用户任务分类导航。
2. 普通界面偏好（主题、字体、默认视图、侧栏宽度、详情面板、记住工作范围、缩略图尺寸）
   改动后防抖保存并显示状态。
3. API Key / Base URL / 任务模型路由 / 供应商等敏感配置保留明确保存区反馈。
4. 移除重复的 `rename_pattern` 控件：`gui/views/settings.py` 中只保留一个真实入口。
5. Prompt 预览不再因刷新而偷偷持久化 `local_prompt`。

## 实现

- 新增 `gui/models/settings_catalog.py`：五类任务分类（日常偏好 / 分析与处理 / AI 服务与模型 /
  提示词 / 命名与导出）+ 全部可编辑设置项的可搜索索引（label、keywords、save_mode、effect、页签）。
- 新增 `gui/services/settings_controller.py`：`SettingsController` 防抖保存普通偏好，
  发出 `pending` / `saved` / `save_failed` 状态信号。
- `gui/views/settings.py`：
  - 顶部设置目录搜索框 + 分类跳转按钮，命中项可跳到对应页签并高亮控件。
  - 普通偏好控件登记后绑定防抖自动保存；复合控件（缩略图尺寸）按容器与子树绑定。
  - AI / Prompt / 导出保留明确保存区，`apply_settings` 行为与配置键不变。
  - `rename_pattern` 唯一真实入口放「常规」，导出页签仅只读镜像。
  - `refresh_prompt_preview` 改为只读展示，不再写回 `service.tag_config`。

## 验收与测试

- `tests/test_settings_catalog.py`（13 项）：目录分类 / 搜索 / 唯一重命名入口；
  视图搜索跳转与分类跳转；普通偏好防抖保存并落盘、状态文案；敏感 Key 显式保存才落盘；
  重命名镜像同步；预览不持久化 `local_prompt`。
- 既有 `tests/test_settings_persist.py`、`tests/test_model_providers.py` 通过；全量 `tests/` 通过。

## Comments

2026-10-05：已实现并通过测试。未修改素材查询 / 标签库文件。
