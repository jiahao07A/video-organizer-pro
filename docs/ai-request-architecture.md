# AI 请求维护说明

## 唯一请求入口

活动代码中的 OpenAI 兼容请求统一由 `core/ai_gateway.py` 的 `AiRequestGateway` 处理。它负责：

- 根据任务模型路由解析供应商连接和模型；
- 按 provider ID、Base URL 和 API Key 缓存客户端，配置变化后避免复用旧连接；
- 关闭 OpenAI SDK 内置重试，统一执行 ADR-0005 的调用层重试和退避；
- 在每次重试前检查分析取消标志；
- 解析普通文本、结构化文本内容、空响应和 JSON；
- 对明确不支持 `response_format` 的兼容服务做一次无该参数的 JSON 解析回退；
- 为聊天和音频转录写入同一套请求失败状态和分析指标。

`core/video_organizer_service.py` 中的 `AIHandler` 只是业务门面，保留 `_get_api_response()` 作为兼容接缝，不应重新实现 HTTP、重试或响应解析。

## 任务路由约定

| 请求用途 | 任务路由 |
| --- | --- |
| 视频帧分析 | `video_classification` |
| 标签生成、标签导入、标签推荐 | `tag_generation` |
| 内容描述相关的音频连接 | `content_description` |
| 待审词 AI | `pending_tag_ai` |
| 近义巡检 | `synonym_audit` |
| 标准词 AI 助手 | `standard_tag_ai` |

音频转录模型固定为 `whisper-1`，但连接档案复用 `content_description`，不新增路由槽。冷启动 AI 分簇默认停用，不应在默认词表流程中发出请求。

## 新增 AI 能力的接入步骤

1. 为既有能力选择对应的六个任务路由槽；没有产品决定时不要新增槽。
2. 通过 `AIHandler._get_api_response(..., task_key="...")` 或直接通过 `AiRequestGateway` 调用，不读取 `api.key`、`api.base_url`、`api.model_personalization` 作为运行时连接真相。
3. JSON 请求只消费网关返回的数据；业务层仍必须校验字段和标签边界。
4. 需要音频转录时调用 `AIHandler.transcribe_audio()`，不要自行创建 `OpenAI` 客户端。
5. 用 fake client 覆盖成功、空响应、瞬时错误、鉴权错误、取消和兼容服务不支持 `response_format` 的情况。
6. 不把 API Key、完整请求内容、真实素材或服务响应写入提交、议题和测试输出。

## 失败语义

- `ApiCallFailure.retriable=True`：允许分析单条层按 ADR-0005 决定是否继续；调用层已经达到上限。
- `retriable=False`：鉴权、参数、权限、文件等不可恢复错误，不进入自动重试。
- `cancelled=True`：用户取消，不再继续调用或批次补跑。
- 标签库 AI 请求失败时由业务门面降级到规则建议；建议仍须人工确认后才能写库。
