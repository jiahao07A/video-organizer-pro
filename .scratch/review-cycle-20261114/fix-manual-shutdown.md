# 人工测试脚本退出生命周期修复

Status: 修复及定向回归完成，待独立复审

## 范围与行为

仅修改 `tools/manual_gui_test.py`，新增 `tests/test_review_manual_shutdown.py`。没有修改正式启动入口、`core/manual_test_environment.py` 或其他代理文件，没有 commit。

原脚本在等待返回 False 或抛错后仍继续调用 `deleteLater()` 和 `environment.close()`，可销毁仍持有活动 QThread 的窗口、提前删除其使用的数据。

- 抽取 `_drain_background_work()` 和 `_close_test_session()`，销毁/会话清理只有一个经过 drain 的入口。
- 先请求服务取消，调用监控既有 `stop(wait=False, shutdown_analysis=True)`，避免 GUI 线程直接阻塞在线程池 shutdown。
- 每次以0.05秒预算调用既有线程等待检查；片段之间处理Qt事件，检查监控分析状态、监控线程及Observer实际存活状态。`first_timeout` 默认10秒，仅用于首次等待提示，不是强制销毁期限。
- 等待超时后保留窗口、线程引用和临时数据，输出继续等待提示，之后每30秒提示一次；检查抛错视为未确认完成，保留现场继续检查，不把异常当成功。
- 至少连续两轮空闲，且中间处理Qt finished回调可能启动的后续任务，才清预览、正常close、deleteLater并清理环境。没有窗口时仍正常清理环境。

## 验证

```powershell
rtk proxy python -X utf8 -m pytest tests/test_review_manual_shutdown.py -q -rs
```

**5 passed in 3.61s**。真实QThread持续超过注入的首个0.01秒期限：Qt定时回调确认线程仍运行、窗口有效、临时数据文件仍存在且环境未close，然后才允许线程完成，最后断言环境清理。参数化覆盖首次检查抛错；另有真实Python监控线程仍存活时禁止清理、没有窗口的正常清理，以及运行实际脚本的离屏子进程正常退出与JSON清理报告验证。没有强杀线程。

```powershell
rtk proxy python -X utf8 -m pytest tests/test_review_manual_shutdown.py tests/test_manual_test_environment.py tests/test_gui_review_regressions.py tests/test_smoke_exit.py -q -rs
```

**56 passed in 36.42s**，无跳过。

```powershell
rtk proxy python -X utf8 -m py_compile tools/manual_gui_test.py tests/test_review_manual_shutdown.py
rtk git diff --check
```

均通过。未运行真实供应商请求或读写用户素材/凭据。

## 限制

协作式等待不会强杀挂死任务；若底层任务永不完成或检查永久异常，脚本继续保留窗口和数据并定期说明状态，不会以提前清理来制造正常退出。首次10秒期限通过注入短期限验证相同代码分支；没有等待真实API超时或验证所有第三方线程组合。Windows10实机未验证。
