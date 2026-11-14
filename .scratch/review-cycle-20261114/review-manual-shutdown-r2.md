# 人工测试关闭修复独立 R2 审查

Status: 发现一个确认问题，需继续修复
审查角色：原 Windows 审查代理

## 已确认问题 MANUAL-R2-01 / P2

位置：`tools/manual_gui_test.py:39`，在 GUI 线程直接执行 `monitor.stop(wait=False, shutdown_analysis=True)`。`wait=False` 只避免监控线程 join 和分析线程池 shutdown 等待，不能使 watchdog 的 `Observer.stop()` 成为非阻塞调用。

当前成熟依赖 watchdog 的真实实现中，`BaseThread.stop()` 同步调用 `BaseObserver.on_thread_stop()` → `unschedule_all()`，后者获取 Observer 的 `_lock`；`dispatch_events()` 同样持有该锁执行应用事件 handler。若事件 handler 正在等待数据库、文件系统或其他阻塞操作，GUI 调用 `monitor.stop()` 就会同步等待该 handler，Qt 事件循环不能响应。生产监控 handler 的 `notify_path()` 会查询数据库，存在真实阻塞入口。

证据：新增只读探针 `probe_manual_observer_shutdown.py` 使用真实 watchdog Observer 和真实 Qt QApplication，Observer 正处理事件时让 handler 等待最多3秒；安排100ms Qt定时器检查窗口/数据并释放 handler。调用真实 `_close_test_session()` 后，Qt定时器被 stop 同步阻塞，直到 handler 自己3秒超时才得到执行，或根本未在cleanup前执行。一次直接输出：

```text
{'cleanup_after_seconds': 2.903,
 'qt_timer_observations': [(False, True, True, False)],
 'observer_stopped': True}
```

预期100ms时 Observer仍存活、窗口有效、数据保留、未清理，并由Qt回调释放；实际回调约3秒后才执行，Observer已退出。探针因预期活动状态断言失败（退出码1）。没有强杀线程，finally让Observer正常停止并join，没有修改产品代码。

建议执行方案：停止监控的调用本身移出 GUI 线程到有明确引用的 Python 停止线程；drain 循环同时等待停止线程、监控线程、Observer、分析池以及Qt子线程，继续处理Qt事件。停止线程异常必须记录，并保留线程/窗口/数据直到所有相关任务确实结束。需新增真实Observer处理事件锁占用测试，不能仅用 `SimpleNamespace.stop=lambda` 证明非阻塞。

## 已通过的独立复核

1. `rtk proxy python -X utf8 -m pytest tests/test_review_manual_shutdown.py tests/test_manual_test_environment.py tests/test_smoke_exit.py -q -rs --tb=short`

   **13 passed in 6.66s**，无跳过。确认既有真实QThread超时、检查异常、普通Python监控线程存活时不会提前destroy/cleanup，最终close一次；但这些测试把monitor.stop替换为立即返回的lambda，漏掉上述真实Observer锁时序。

2. 真实离屏启动失败子进程探针：把 `--diagnostics-root` 指向一个明确的现存普通文件，使 environment.start 在创建诊断目录阶段失败。

   结果：退出码1，`临时测试环境启动失败` 原始错误可见；没有二次 traceback；sentinel原文件保留，finally能返回幂等清理报告。此检查没有启动可见窗口或访问用户数据。

3. 超时/检查异常保护总体方案正确；连续两轮空闲和处理Qt finished回调后再销毁，未发现该部分的确认数据提前清理回归。尚须解决上述stop调用阻塞，不能把当前R2标为完全通过。

## 重现命令

```powershell
rtk proxy python -X utf8 .scratch/review-cycle-20261114/probe_manual_observer_shutdown.py
```

本轮只写本报告与独立探针，未修改产品/已有测试、未提交、未启动可见窗口、未删除目录。
