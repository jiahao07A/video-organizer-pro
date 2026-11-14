# 人工退出停止线程 R3 独立验收

Status: 通过；未发现此次修复的确认回归
审查角色：原 Windows 审查代理

## R2 问题关闭证据

未改动原真实 Observer 复现探针，直接重跑：

```powershell
rtk proxy python -X utf8 .scratch/review-cycle-20261114/probe_manual_observer_shutdown.py
```

结果退出码0：

```text
{'cleanup_after_seconds': 0.135,
 'qt_timer_observations': [(True, True, True, False)],
 'observer_stopped': True}
{'observer_alive_window_valid_data_present_not_cleaned': [(True, True, True, False)],
 'cleanup_calls': 1, 'observer_stopped': True}
```

同一探针在R2旧实现约2.9秒才响应Qt回调，且回调观察到Observer已停止，断言失败；当前100ms Qt回调能在线程仍运行时检查窗口和数据并释放handler，随后Observer与停止线程退出，最终清理恰好一次。真实锁阻塞不再发生在GUI线程。

## 独立定向回归

```powershell
rtk proxy python -X utf8 -m pytest tests/test_review_manual_shutdown.py tests/test_manual_test_environment.py tests/test_smoke_exit.py -q -rs --tb=short
```

结果：**15 passed in 6.94s**，无跳过。包括既有QThread超时/检查异常保护、Python监控线程等待、实际脚本离屏退出，以及新真实Observer锁阻塞和停止异常重试场景。

## 实际代码复核

- `monitor.stop()` 本身由非守护Python停止线程执行，局部变量 `stop_thread` 在drain整个生命周期保有强引用；代码只在其已不存活后join并读取结果，之后才可能返回。
- 停止线程失败不设置 `succeeded`，主Qt线程输出原异常、保留数据，0.1秒后创建新的停止线程重试。线程启动失败也同样延迟重试，没有把失败默认为空闲成功。
- monitor存在时，只有停止成功、停止线程退出、监控主线程/Observer退出、analysis_running为假且既有widget线程检查通过，才累计空闲次数。仍需连续两轮空闲，中间处理Qt事件。
- `_close_test_session()` 在drain返回前不调用窗口close/deleteLater或environment.close；新异常测试令原本所有业务线程空闲但stop一直失败，实际证明直到Qt允许后续stop成功前窗口和数据仍保留。
- 重试不形成并发停止线程：失败线程退出并join后才清空引用并安排后续尝试；成功线程引用保留至整个drain结束，不存在停止线程仍使用服务而窗口已销毁的本次修复竞态。

## 边界

未运行可见GUI、冻结包或真实供应商请求；没有改产品/现有测试、没有提交或再派代理。协作取消仍不能强杀永久阻塞的第三方任务；永久失败时保留窗口/数据并持续等待是本次明确产品行为，不算已验收所有外部线程组合。Windows10实机未验证。
