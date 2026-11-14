# R3 人工测试退出停止线程修复

状态：实现与作者验证完成，等待 Windows 退出流程独立复审。未提交 Git。

## 问题与边界

- `tools/manual_gui_test.py::_drain_background_work` 在 Qt 主线程同步调用 `monitor.stop(wait=False, shutdown_analysis=True)`。
- watchdog Observer 正在 handler 中持事件分发锁时，`stop()` 仍会等待锁；`wait=False` 只控制后续 join，不能保证 stop 本身不阻塞。
- 停止异常原先仅输出日志，后台本来空闲时仍可能立即清理，缺少“停止请求成功”门槛。
- 本次仅修改 `tools/manual_gui_test.py`、`tests/test_review_manual_shutdown.py`，不修改监控产品模块，不涉及用户真实数据。

## 任务清单

- [x] 运行真实 Observer 红探针，复现约 2.967 秒停止阻塞。
- [x] 先添加真实 Observer/Qt 与停止异常重试测试，确认两项均在原实现失败。
- [x] 将 stop 移到持有明确局部引用的非守护 Python 线程。
- [x] 只有停止线程成功返回且已退出、监控线程及 observer 已退出、analysis_running 为 False，才能进入连续两次空闲确认。
- [x] 停止失败记录异常，间隔 0.1 秒重试；失败期间禁止清理。
- [x] 保留请求取消、短时间分段等待、Qt 事件泵、超时继续等待提示。
- [x] 原真实探针转绿；退出和临时环境测试 10 项通过；ruff 和 diff 检查通过。
- [ ] 非作者独立复审。

## 验证证据

1. 红探针：

   `rtk proxy python -X utf8 .scratch/review-cycle-20261114/probe_manual_observer_shutdown.py`

   原实现 exit=1，cleanup_after_seconds=2.967；100ms Qt 回调观察到 observer 已退出，说明回调一直被同步 stop 阻塞。

2. 回归测试先红：

   `rtk proxy python -m pytest tests/test_review_manual_shutdown.py -k "real_observer_stop or retries_stop_errors" -q`

   原实现 2 failed：真实 Observer 定时器只能在 observer 结束后执行；停止抛错后未等待成功就调用环境清理。

3. 同两项修复后 2 passed。真实 Observer 测试使用 watchdog Observer，并让 handler 最多等 3 秒；finally 必定释放事件并 stop/join，避免坏实现挂住测试。清理点断言实际 stop 调用线程为非主线程、非守护且已结束。

4. 原探针转绿：exit=0，cleanup_after_seconds=0.142；Qt 回调观察 `(observer活着=True, 窗口有效=True, 临时数据存在=True, 已清理=False)`，随后才清理，最终 observer 已结束。

5. `rtk proxy python -X utf8 -m pytest tests/test_review_manual_shutdown.py tests/test_manual_test_environment.py -q`：10 passed in 6.40s，包含 offscreen 实际主程序子进程退出与 cleanup-report。

6. `rtk proxy python -m ruff check tools/manual_gui_test.py tests/test_review_manual_shutdown.py`：All checks passed。

7. `rtk git diff --check`：exit=0。

## 复审重点与限制

- 失败停止不能被“监控本来空闲”绕过；重复失败时仍需继续处理 Qt 事件并保留数据。
- 活动的非守护停止线程不能触发清理；不通过 daemon 脱离生命周期。
- 当前验证为本机 Windows 环境和真实 watchdog/PySide6，没有新增 Windows 10 实机结论。
- 持续底层故障会一直保留窗口与会话，符合本任务“不强制销毁活动线程”的约束。
