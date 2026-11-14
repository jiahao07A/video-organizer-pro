# 监控暂停/恢复测试环境隔离复核

## 原始证据与判断边界

主 Agent 完整测试结果曾为1 failed、1150 passed、9 skipped，唯一失败为 `tests/test_media_watch_workspace.py::test_pause_keeps_undispatched_items_queued[False]` 的 `wait_monitor` 5秒超时。主 Agent 提供日志显示第二个任务约4.9秒后仍需指标/DB收尾，同时构建与全量验证存在资源竞争。

在任何修改前，独立重跑该测试两个参数，结果 **2 passed in 7.77s**。这证明无法稳定单独复现，但不能推导产品无错，也不足以100%确定超时根因。

源码可确认的测试隔离缺口：原 service fixture 只隔离数据库、settings、tags；`run_analysis` 会调用真实 `setup_analysis_file_logging()`，后者复用全局 DiagnosticRuntime 或在 LOCALAPPDATA 建默认runtime。`persist_job_metrics()` 默认写入该runtime的 analysis_jobs，且调用真实 prune。测试因此可依赖前序用例/用户累积的日志量与全局runtime状态。

## 本次修改范围

仅修改 `tests/test_media_watch_workspace.py`：

- service fixture 新增依赖真实 `diagnostic_runtime` fixture，每项用例使用自己的 `tmp_path/diagnostics`；指标环境变量明确指向同一临时目录的 analysis_jobs。
- fixture开始保存并flush既有runtime，保存logger handlers/level/propagate；用例结束关闭本次真实runtime并恢复既有全局状态，不关闭或搬迁此前runtime。
- 新增真实隔离回归，实际执行指标事件、JSON汇总、JSONL索引及诊断落盘，检查runtime/metrics路径均在该用例tmp目录。
- 未mock prune、persist_job_metrics或诊断runtime；未增加任何5秒/3秒超时，未删改暂停/恢复队列、未分发项保持queued及已保存结果等原业务断言。
- 未改产品源代码、版本、正式包或其他测试文件，未commit。文件已冻结并通知主 Agent。

## 本次验证结果

1. 修改前独立复跑原失败测试：**2 passed in 7.77s**。
2. 修改后完整模块：
   `rtk proxy python -X utf8 -m pytest tests/test_media_watch_workspace.py -q -W error::pytest.PytestUnhandledThreadExceptionWarning`
   **35 passed in 27.69s**。
3. 真实暂停/恢复/持久化队列定向再次验证：
   `rtk proxy python -X utf8 -m pytest tests/test_media_watch_workspace.py -q -k 'pause or resume or persistent' --durations=8 -W error::pytest.PytestUnhandledThreadExceptionWarning`
   **7 passed, 28 deselected in 8.28s**；原失败测试 `[False]` call 1.13秒、`[True]` call 1.12秒。

上述为测试环境隔离修复与模块/定向验证，不能冒充完整suite重新通过。主 Agent 将在独立LOCALAPPDATA环境重新执行完整suite；最终全量结论以该次真实执行结果为准。没有将日志耦合宣称为100%已证明的唯一根因。
