# 独立需求审查与停止超时补正

Status: completed-awaiting-main-review

执行者：exit_spec_review；Issue #90；分支 codex/update-exit-handoff。

## 已确认问题

`MediaDirectoryMonitor.stop(wait=True)` 对监控线程和 Observer 使用10秒join，但不检查存活状态，可能返回成功；主窗口随后接受关闭并退出。内存隔离探针使用真实阻塞线程得到 `elapsed_seconds=10.0, monitor_thread_still_alive=True, analysis_shutdown=True`，违反“监控线程结束再交接”的验收。探针释放Event后已正常回收线程，无用户数据和进程操作。

## 主审确定的执行包

- 仅修改 `core/media_watch.py`、新增 `tests/test_media_watch_shutdown.py` 和此记录，不commit。
- `stop(wait=True)` 等待并逐个检查monitor/Observer，超时中文TimeoutError说明仍在停止、稍后重试。
- 超时保留stop事件，不继续关闭分析池，不强杀线程；线程自然完成后可重试stop。
- `wait=False` 保持既有语义，不重构分析池无限等待。
- 使用真实Thread/Event先红后绿；常量缩短测试等待。验证新文件及原 `tests/test_media_watch.py`，独立新basetemp。

| 任务 | 状态 |
| --- | --- |
| 独立需求审阅及阻塞线程复现 | 完成 |
| 真实线程回归先红 | 完成：monitor/observer均未抛TimeoutError而失败，2 failed、1 passed |
| 超时处理补正 | 完成：有界等待后检查真实存活状态，超时先返回错误且保留分析池 |
| 新旧监控测试与静态检查 | 完成：20 passed；Ruff E9/F821/F822/F823通过 |

## 验证记录

- 红：`rtk proxy python -X utf8 -m pytest tests/test_media_watch_shutdown.py -q --tb=short --basetemp=.release-build/watch-stop-red-a` → **2 failed, 1 passed in 20.62s**。monitor/observer两个真实阻塞线程均出现“DID NOT RAISE TimeoutError”；wait=False语义检查通过。
- 绿：`rtk proxy python -X utf8 -m pytest tests/test_media_watch_shutdown.py tests/test_media_watch.py -q --tb=short --basetemp=.release-build/watch-stop-green-a` → **20 passed in 1.39s**。
- 静态：`rtk proxy python -X utf8 -m ruff check core/media_watch.py tests/test_media_watch_shutdown.py --select E9,F821,F822,F823` → All checks passed。
- 检查点：超时后真实线程仍活、stop事件保留、cancel_batch尚未置位、analysis_shutdown=False，分析池仍能提交并完成任务。释放Event后重试stop成功，分析池线程结束且拒绝新提交。wait=False保持原异步停止/关闭分析池语义。
- 未commit；没有修改GUI、update_exit测试/探针或其他代理文件；没有终止用户进程，也没有操作真实配置、数据库、媒体或发布资产。
