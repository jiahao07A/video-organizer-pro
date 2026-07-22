# 04 — 模型供应商档案（最多五个）

**What to build:** 设置中可管理最多五个**模型供应商**档案（显示名、API Key、Base URL、分类/标签/描述任务模型名）；任意时刻一个**当前供应商**。切换并保存后，分析与标签库侧 AI 使用当前档案。旧单套 API 配置自动迁为显示名「默认」的当前供应商。档案为配置真相来源（可短期写穿旧扁平字段过渡）。进行中分析不因切换而中断本轮。

**Blocked by:** None — can start immediately

**Status:** resolved

**Parent:** [spec.md](../spec.md)

## Acceptance criteria

- [x] 加载无档案时：从旧 Key/URL/三模型迁成「默认」档案并标为当前；可单测
- [x] 设置 UI：列表/切换当前、编辑字段、新增、删除；保存落盘
- [x] 最多五个：第 6 个新增被拒绝并提示
- [x] 删除当前供应商后自动切到另一档案；至少保留可编辑入口（含空档案策略符合 CONTEXT）
- [x] 保存后 AI 调用使用当前供应商的 Key/URL/模型名（分析与标签库 AI 同源）
- [x] 解析/迁移/上限逻辑有单测（S3）；不依赖真实网络

## Comments

### 2026-07-21 to-tickets

- 用户批准 7 票发布；主接缝 S3；frontier

### 2026-07-21 implement

- 新增 `core/model_providers.py`：MAX=5、legacy 迁移「默认」、ensure、resolve、写穿 `api.*`、add/remove/set_current
- `SettingsManager.load_settings` / `save_settings` 与 `reload_ai_from_settings` 钩子 ensure
- `gui/views/settings.py` AI 页：供应商列表 + 设为当前 / 新增 / 删除 + 字段编辑
- `tests/test_model_providers.py` + 既有 persist：13 passed
- 未改 operation_targets / 标签库 UI；未 commit