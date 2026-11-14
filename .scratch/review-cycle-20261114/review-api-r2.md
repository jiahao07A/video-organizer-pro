# API 修复独立复审 R2

状态：完成；本次修复范围未发现可确认回归。只读复审未修改产品源码或现有测试，未提交 Git。

## 清单与范围

- [x] 核对 fix-api-retry 与 fix-api-diagnosis 对应差异。
- [x] 运行真实 OpenAI SDK → 回环 HTTP 请求及诊断持久缓存验证。
- [x] 检查 401/403、取消、普通调用次数及 HTTP 400 恢复总预算。
- [x] 核对诊断实际出站内容脱敏和旧脱敏断言没有被削弱。
- [x] 独立追加诊断 HTTP 500、预取消探针并保存 JUnit 证据。

复审基线 dd609f8，当前工作分支 fix/review-hardening-20261114。其他代理并发修改的迁移、重命名、FCPX、GUI 内容不属于本次结论。

## 差异结论

1. `core/analysis_job_policy.py:109,125` 的显式 HTTP 状态识别增加 408、500。文件错误最前拒绝、字典取消标记最前拒绝、显式 401/403 拒绝及原重试次数门禁保持有效。新增测试没有借助关键词绕过显式状态。
2. `core/video_organizer_service.py:4902` 把诊断 content_parts 中的 JSON 字符串封装为 `{"type":"text","text":...}`。真实 SDK 序列化后的 HTTP 请求被严格服务端接收；正常诊断、结果落库与第二次缓存复用有效。
3. 诊断仍使用 `call_extra_attempts=0`、`item_max_attempts=1`、禁用批次重跑。独立探针让上游返回 500 后预备 200，实际只收到一次请求，结果失败且缓存为空；预取消后仍无新增请求。
4. `tests/test_analysis_failure_center.py:56` 只把 capture 读取调整为 text 字段，原敏感信息排除断言保留。新增契约测试从真实失败记录经过服务、网关、SDK 捕获实际 HTTP JSON，确认合成密钥、完整路径、data image/base64 均不出站，同时保留 HTTP 503、阶段、供应商、模型及 timeout 等诊断依据。
5. HTTP 400 恢复的上下文预算、鉴权跳过、取消中止、跨视觉/音频共享预算等原集成用例在本轮全部通过；没有因新增 408/500 重试分支放松 400 恢复预算的证据。

## 验证证据

环境：Windows 11 x64，Python 3.13.12，PySide6 6.10.2，OpenAI SDK 2.20.0，pytest 9.1.1。

```text
rtk proxy python -X utf8 -m pytest tests/test_review_api_retry.py tests/test_review_diagnosis_contract.py tests/test_analysis_job_policy.py tests/test_ai_gateway.py tests/test_http400_recovery.py tests/test_analysis_failure_center.py -q -rs --junitxml=.scratch/review-cycle-20261114/review-api-r2-junit.xml
170 passed in 98.80s (0:01:38)

rtk proxy python -X utf8 -m pytest .scratch/review-cycle-20261114/api_r2_probe.py -q -rs --junitxml=.scratch/review-cycle-20261114/review-api-r2-probe-junit.xml
1 passed in 3.14s
```

无失败、无跳过。首次启动的同套件在上下文压缩后丢失输出句柄；确认进程结束后才重新运行上述带持久证据的套件，报告不采用缺失的首次结果。

新增回环用例验证：408/500 → 200 恰好两次；持续 408/500 在额外次数 0/2 时恰好 1/3 次；401/403 即使有额外预算也仅一次；首次 500 后取消不再发第二次；服务器线程最终关闭。独立探针位于 `api_r2_probe.py`。

## 边界

结论针对上述两个 API 修复和已运行用例。请求仅发往 127.0.0.1，无真实凭据、用户素材或公网 AI 请求；出站脱敏证据覆盖合成密钥、路径与图片载荷，不代表对任意未知敏感格式作了穷举证明。本轮没有 Windows 10 实机或第三方供应商线上稳定性证据，不将本机通过表述为这些环境已验证。GUI 取消入库与恢复门禁另由主代理安排后续复审。
