# R1-B：AI 接口、路由与恢复审查

Status: 第一轮只读审查完成，发现两项需修复问题
Baseline: dd609f8
环境：Windows 11 x64 / Python 3.13.12 / OpenAI SDK 2.20.0。

## 已确认问题

### API-01 [P1] 错误诊断请求内容不符合 Chat Completions 契约

- 定位：`core/video_organizer_service.py:4900`；`core/ai_gateway.py:464` 原样发送该内容。
- 触发：失败处理中心主动执行错误诊断，且请求未命中诊断缓存。
- 实际：入口传入 `[json.dumps(payload)]`，SDK 实际发出的 `messages[1].content` 是字符串数组。数组形式的内容须为内容对象（例如 `{"type":"text","text":"..."}`）；与本仓库其他生产调用格式不同。
- 证据：`api_probe.py` 中使用真实 `service.diagnose_analysis_failures(force=True)`、真实 OpenAI SDK、校验该字段形状的回环 HTTP 服务器。结果 `succeeded=0, failed=1, requests=1`，HTTP 400；同样的 JSON 改用 text part 后成功收到 200。
- 测试缺口：`tests/test_analysis_failure_center.py:45` 的出站脱敏测试替身了 `_get_api_response`，且 `:56` 的 `json.loads(content[0])` 实际固化了错误形状。缓存、分组及诊断交互测试同样绕过真实传输，故 280 个定向测试全绿仍漏检。
- 建议：仅将诊断入口改成 `[{"type":"text","text":json.dumps(payload, ensure_ascii=False)}]`；同步既有脱敏测试读取 `content[0]["text"]`；增加经真实 SDK/回环 HTTP 的业务入口测试，验证 HTTP 请求结构、实际诊断成功及缓存写入。

### API-02 [P2] HTTP 408/500 无法进入有界重试

- 定位：`core/analysis_job_policy.py:108` 和 `:124` 附近的状态码判定；`core/ai_gateway.py:191` 明确设置 SDK `max_retries=0`。
- 触发：服务返回 HTTP 408 Request Timeout 或 500 Internal Server Error，下一次请求可以成功。
- 实际：408 命中整个 4xx 不可重试分支；500 没有命中 429/502/503/504，也无重试文本标识，因此均被判为永久失败。鉴权策略优先于文本的设计正确，但超时/服务端内部错误缺少对应分支。
- 证据：真实 SDK 回环 HTTP 配置先返回该错误、第二次返回成功，允许额外一次调用重试：408/500 均只发 1 次并失败、`retriable=false`；429/503 发 2 次并成功。SDK 重试关闭，因此无法在下层弥补。
- 建议：408、500 加入明确可重试状态码集合（对象/字典两条路径保持一致）；保留 400/401/403/404/422 不重试、预算和取消边界。以真实 SDK/HTTP 验证两者恢复与耗尽上限，避免只对错误字符串断言。

## 本轮验证

命令：

```powershell
rtk proxy python -X utf8 -m pytest -q -rs tests/test_ai_gateway.py tests/test_model_providers.py tests/test_analysis_job_policy.py tests/test_analysis_result_validation.py tests/test_http400_recovery.py tests/test_analysis_orchestration_retry.py tests/test_analysis_failure_center.py tests/test_analyze_operation_pipeline.py tests/test_audio_transcriber.py tests/test_analysis_concurrency.py
```

结果：**280 passed in 306.21s**；无跳过项。此结果只表示上述既有断言通过，不表示 API-01/02 正常。

```powershell
rtk proxy python -X utf8 .scratch/review-cycle-20261114/api_probe.py
```

直接结果见 `api-probe-results.json`：正常请求、429/503 重试、response_format 不兼容重发成功；401/403 不重复请求；408/500 和真实诊断入口失败，如上。

## 覆盖与边界

- 阅读并核查网关、供应商路由、三层重试策略、400 恢复上下文与调度器、失败中心、分析结果校验、标签建议、共享执行门禁及视频/音频编排调用链。
- 定向测试覆盖路由冻结、切换连接不打断在途请求、429/503/超时、取消、400 请求预算、参数兼容重发、恢复历史、旧结果保留、成功事务与副作用、音频临时文件隔离及结果拒绝。
- 未验证公网供应商和真实收费调用，未读取用户密钥/素材/数据库；音频请求在现有测试中使用替身，未声称完成真实供应商转录。
- 当前只有 Windows 11 证据，无 Windows 10 实机证据。
- 本会话未提供 Context7 工具，未安装替代插件；API 契约结论依据实际 SDK 出站请求、仓库正常调用形状及回环验证，未声称取得线上文档。
- 未修改产品或现有测试、未提交。新增本报告与隔离探针由主 Agent 复核后决定修复分工。
