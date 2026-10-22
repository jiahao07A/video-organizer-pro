# [体验改进] AI 供应商协议与模型选择

Status: claimed
GitHub Issue: #21 — https://github.com/jiahao07A/video-organizer-pro/issues/21
Branch: `feat/21-settings-effective-config`
PR: [#33](https://github.com/jiahao07A/video-organizer-pro/pull/33)

## 背景和目标

现有模型供应商档案统一调用 Chat Completions，并由用户手动输入模型 ID。扩展 AI 设置，使用户可为每个供应商选择 Chat Completions 或 Responses API，获取该供应商可用模型列表，并仍可手动输入自定义模型 ID；连接测试真实调用所选模型。

## 范围内

- 供应商协议字段；旧档案缺失协议时兼容为 Chat Completions。
- 六个任务路由按选定供应商协议获取模型列表，选择模型或直接输入自定义 ID。
- 网关按供应商协议执行 Chat Completions 或 Responses 请求；转换现有文字/图像输入并解析结构化结果。
- 使用任务路由的当前草稿模型发起最小连通性推理请求；显示协议和可操作错误，敏感凭据不进入结果。
- 保存配置、进行中请求和旧配置迁移行为保持不变。
- 设置页信息密度调整：顶部摘要不重复六个路由，任务路由逐行显示实际配置；供应商默认模型移至折叠的高级回退区，使用任务分类作为唯一主导航，重命名编辑入口归入“命名与导出”。

## 范围外

- 不增添任务路由槽，不增加五个供应商上限。
- 不在协议失败时静默切换另一协议。
- 不自动保存拉取的目录缓存或模型选择；用户选择/输入的模型仅在显式保存 AI 设置后落盘。
- 不改 API Key 的存储方案，不引入非 OpenAI 协议。
- 不包含 #10 数据目录实现、人工测试环境、本机数据库/设置/日志或构建产物。

## 验收标准

- 无协议字段的旧配置仍解析为 Chat Completions。
- 每个供应商协议独立配置，任务路由选择任意供应商后均使用该档案协议。
- 每项任务可获取所解析供应商的模型 ID 列表并选择，也可输入目录之外的自定义 ID。
- 获取目录失败不覆盖现有模型值；测试请求使用草稿、不落盘、不暴露密钥。
- Chat Completions 和 Responses 的文本/图片输入、JSON 输出和失败状态均有网关测试。
- 原有供应商上限、路由回退及设置保存回归测试通过。
- 六个任务路由的实际供应商和模型仅在对应行展示；顶部摘要不重复展开六条信息。
- 供应商默认模型区域默认折叠；设置分类导航、重命名唯一编辑入口及搜索定位均通过回归测试。

## 依赖与约束

- 依赖 ADR-0006 的五档案、六槽路由与默认回退规则。
- 当前环境 OpenAI Python SDK 为 2.20.0，已确认存在 `responses.create` 与模型列表分页 API。
- 连通性测试发送极小无用户数据请求，可能产生少量费用；界面需清楚提示。

## 风险与回滚

兼容代理可能未实现 Responses、模型列表端点或所选模型能力。分别显示模型列表失败与真实推理失败；不隐式变更协议。新增协议值可回滚为 Chat Completions，旧配置仍按默认值工作。

## 验证计划

- 运行 `tests/test_model_providers.py`、`tests/test_ai_gateway.py`、`tests/test_settings_catalog.py` 与相关 GUI 回归测试。
- 验证 Responses 图片数据 URL 转换和 JSON 响应解析，网关 fake 测试不访问外网。
- 运行完整测试套件、Python 编译检查与 `git diff --check`。
- 可见 Windows GUI 走查需使用隔离临时人工测试环境；不接入本机数据、凭据或真实视频素材。

## Comments

### 2026-10-22 范围与验证记录

本地规格已按 GitHub Issue #21 的范围决定补入供应商协议选择、任务模型发现与真实连接测试；数据目录功能留在 #10 分支。分支 `feat/21-settings-effective-config` 从 `origin/main` 创建。

- `python -m pytest tests/test_ai_gateway.py tests/test_model_providers.py tests/test_settings_catalog.py tests/test_gui_review_regressions.py -q`：100 passed。
- `python -m pytest -q`：377 passed。
- `python -m compileall -q core gui tests`：通过。
- `git diff --check`：通过。
- 可见 Windows GUI 人工走查和真实供应商连接测试尚未执行；外部 API 的可用性/费用仍待人工确认。
