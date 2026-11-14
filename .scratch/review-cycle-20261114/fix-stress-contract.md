# HTTP500 压力测试契约同步

状态：修复完成，定向真实 HTTP 压力验证通过。

任务清单：

- [x] 阅读原压力测试和 `stats["attempts"]` 的任务计数结构。
- [x] 修改前单独运行 HTTP500 参数，验证旧期待确实失败。
- [x] 仅同步故障重试用例的成功/请求次数期待，增加逐任务次数约束。
- [x] 保持并发、任务规模、超时、启用门槛和真实网络路径，运行 HTTP429/500 两项。

原因：产品修复后 HTTP500 与 HTTP429 均可在已有额外一次调用预算内重试。此用例的服务端为每个唯一任务第一次返回指定故障、第二次返回 HTTP200；原 HTTP500 分支仍期待全部失败，与当前产品契约不符。

修改前执行：

```powershell
rtk proxy pwsh.exe -NoProfile -Command '$env:VOP_RUN_STRESS = "1"; rtk proxy python -X utf8 -m pytest "tests/test_analysis_stress.py::test_gateway_faults_preserve_retry_policy[500]" -q --tb=short'
```

结果：`1 failed in 4.11s`，原第 226 行期待 `successes == 0`，实际为 128。失败原因为过时的断言。

修改仅位于 `tests/test_analysis_stress.py::test_gateway_faults_preserve_retry_policy`：两个参数均明确要求成功 128、实际请求 256；要求恰好 128 个唯一任务，且每个任务的 `attempts` 必须恰好为 2。任务键来自请求最后一条消息 content 的 JSON，独立统计由真实 HTTP 服务端维护，能够发现遗漏任务、重复执行及超预算重试。保留单一客户端复用断言。

修改后执行：

```powershell
rtk proxy pwsh.exe -NoProfile -Command '$env:VOP_RUN_STRESS = "1"; rtk proxy python -X utf8 -m pytest tests/test_analysis_stress.py::test_gateway_faults_preserve_retry_policy -q --tb=short'
```

结果：`2 passed in 6.98s`。两项保持 64 个工作线程、128 个任务，分别得到 128 次故障及 128 次 HTTP200，均成功 128、请求 256。HTTP429 场景采样 3.024 秒，HTTP500 场景 3.402 秒。使用真实 OpenAI SDK 与随机回环 HTTP 服务，无网络 mock。

本轮没有修改产品代码、其他测试、并发规模、超时或跳过门槛，没有提交 commit。持续 HTTP500 的预算耗尽由现有 `test_review_api_retry` 独立覆盖，本轮未重复新建该测试。
