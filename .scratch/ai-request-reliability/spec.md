# 统一 AI 请求并修复请求旁路与客户端生命周期

Status: claimed
GitHub Issue: #4
PR: #5
Branch: refactor/ai-request-reliability

## 背景

活动代码中的 AI 请求由多个入口分别处理。视频分析和标签库 AI 复用了部分 `_get_api_response`，但标签导入和音频转录仍绕过任务模型路由、统一重试与错误处理；供应商 Key/Base URL 更新后也可能继续使用旧客户端。不同 OpenAI 兼容服务对 `response_format` 的支持不一致，导致请求失败难以定位和维护。

## 目标

建立唯一的 AI 请求网关，统一活动代码中的聊天请求与音频转录请求的供应商路由、客户端生命周期、调用层重试、取消检查、响应解析和错误状态；修复已确认的旁路调用，降低后续新增 AI 请求的维护成本。

## 范围内

- `core/` 活动代码中的 OpenAI 请求入口；不修改 `backups/`、`dist/`、`build/`。
- 视频分析、标签库 AI、标签导入 AI、音频转录的路由与请求处理。
- OpenAI 兼容响应形状、空响应、JSON 兼容回退和供应商配置更新后的客户端失效处理。
- 纯逻辑单元测试和请求网关说明文档。

## 范围外

- 新增模型供应商类型或改变六个任务模型路由槽定义。
- 改变标签 AI“建议后人工确认才写库”的边界。
- 启用冷启动 AI 分簇。
- 重命名、导出、XMP 等非 AI 请求流程的整体重构。
- 真实 API Key、真实视频和在线 API 作为自动化测试依赖。

## 已确认的问题

1. `core/video_organizer_service.py` 中 `AudioTranscriber.transcribe()` 直接创建 `OpenAI` 并调用转录接口，绕过任务模型路由、统一重试、取消和指标。
2. `core/tag_import_service.py` 直接读取扁平 `api.model_personalization` 并调用 `_get_api_response()`，没有传 `task_key`，因此可能使用错误供应商/模型。
3. `AIHandler.client_for_task()` 只按供应商 ID 缓存客户端，凭据或 Base URL 变化时若未完整重载可能继续使用旧客户端。
4. `_get_api_response()` 将 JSON 输出能力绑定到 `response_format`，未兼容不支持该参数的 OpenAI 兼容服务；同时对空 `choices` 和结构化消息内容的诊断不统一。
5. `cluster_cold_start_words()` 保留真 AI 代码，但产品规定冷启动 AI 默认停用；活动入口必须继续阻止默认在线请求。

## 已决定的方案

- 新增 `core/ai_gateway.py` 作为唯一传输层：负责按任务解析连接、按连接配置缓存/失效 OpenAI 客户端、聊天 JSON/文本请求、音频转录请求、调用层重试、取消检查、响应提取和失败状态。
- 保留 `AIHandler` 的兼容门面和 `_get_api_response()` 签名，内部委托网关，避免一次性改动所有业务调用；生产调用必须提供任务路由槽。
- 视频分析沿用 `video_classification`；标签库 AI 沿用各自六槽；标签导入使用 `tag_generation`；音频转录复用 `content_description` 的供应商连接但固定 `whisper-1`，不新增路由槽。
- JSON 请求遇到明确表示不支持 `response_format`/`json_object` 的 400 错误时，只做一次无 `response_format` 的兼容重发，再按同一 JSON 解析规则处理；其它 4xx 不重试。
- 调用层重试继续使用 ADR-0005 的 `RetryConfig`，不叠加 OpenAI SDK 内置重试；音频转录也使用同一调用层策略。
- 供应商连接缓存键包含 provider ID、Base URL 和 API Key，且显式 reload 会清空缓存；不记录 API Key。
- 冷启动 AI 入口默认只走直接加载/规则路径，不发请求。

## 验收标准

1. 活动代码除统一网关外不直接创建 OpenAI 客户端或调用聊天/转录接口。
2. 视频分析、标签库 AI、标签导入和音频转录的供应商连接均由统一路径解析。
3. 鉴权、用户取消和文件错误不重试；瞬时错误仍按 ADR-0005 上限重试。
4. 不支持 `response_format` 的兼容服务可通过无该参数的兼容回退解析合法 JSON；其它 4xx 不被误重试。
5. 供应商 Key、Base URL 或路由更新后新请求不会继续使用旧客户端，进行中的请求不被强行中断。
6. 空响应、空 choices、结构化文本内容和非法 JSON 有一致、可诊断的失败状态。
7. 现有行为不回退，并新增网关、路由旁路、客户端失效和音频请求的注入测试。

## 风险与回滚

风险主要是 OpenAI SDK 版本和第三方兼容服务的响应差异，以及转录请求新增统一重试后的请求次数变化。网关、业务接线、测试按可回退的独立提交组织；失败时回滚接线提交即可恢复旧入口。真实视频、外部服务和本机配置不在代码回滚范围内。

## 验证计划

- `py -m compileall core gui tests`
- 环境可用时运行 `py -m pytest -q` 和 AI 相关测试。
- 使用 fake client 覆盖路由、重试、JSON 兼容回退、响应提取、取消和音频转录。
- `git diff --check`、检查活动代码中的直接 OpenAI 调用清单。

## Comments

### 状态更新

- 已读取 `CONTEXT.md`、相关 ADR、Subagent 工作规范、Issue 追踪规范和现有 AI/分析测试。
- 初始基线未能运行：当时 Python 环境缺少 `pytest` 模块；已安装测试依赖后完成请求/分析相关验证。
- GitHub Issue 创建尝试因当前网络无法连接 GitHub API 失败，待网络恢复后补建并回填编号。
- 已新增 GitHub Issue #4，作为本任务共享追踪身份。
- 已新增 `core/ai_gateway.py`，并将视频分析、标签库 AI、标签导入和音频转录接入统一网关。
- 已补充 `response_format` 兼容回退、空响应解析、供应商连接缓存失效和空响应重试规则。
- 验证：`py -m compileall -q core gui tests` 通过；AI/分析/核心服务相关测试累计 92 个通过。
- 完整 `py -m pytest -q` 仍受环境限制：6 个 GUI 测试模块因未安装 `PySide6` 无法收集；与本次请求层无关的测试未被改动。
