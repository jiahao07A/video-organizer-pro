# 扫描进度 GUI 实施与验证

状态：实现完成，限定回归通过，待主 Agent 集成和独立复审。

## 修改

- `gui/workers/io_worker.py` 保留既有 `(int, str)` 信号，新增结构化 `progress_snapshot` 和可选 `on_progress`。线程启动前连接主线程 QObject relay，显式队列交付；后台扫描只发信号。
- `gui/views/workstation.py` 工作范围扫描使用服务的可选进度回调。总条显示 0.1% 粒度总体进度，阶段条显示阶段完成数/总数；枚举尚无分母时显示忙碌条并持续显示已发现数量。状态文字显示阶段、耗时、发现/新增/重复/失败数。成功、异常均隐藏并恢复原有分析进度条格式和控件状态，成功后再加载目录页。
- 原 GUI 后台扫描替身只增加 `progress_callback=None` 参数，断言保持不变。
- 新增 `tests/test_scan_progress_gui.py`：5 次真实 QThread 即时发送和主线程接收，确认进度不会被结束清理吞掉；成功/失败各覆盖未知分母、阶段数量、总体百分比、耗时、统计、主线程交付、后台完成后控件恢复。测试只操作隔离的 offscreen Qt 控件，不控制可见桌面。

## 验证

命令：`rtk proxy python -X utf8 -m pytest tests/test_scan_progress_gui.py tests/test_review_gui_actions.py tests/test_rename_preview_lock.py -q -W error::pytest.PytestUnhandledThreadExceptionWarning`

结果：69 passed in 47.30s。包含已有真实后台扫描入口与合成视频预览/重命名回归。

`rtk git diff --check`：通过。

## 边界

本报告证明 GUI 的线程交付和状态变化，不把替身扫描耗时当性能验收；1455 视频性能基准和原素材验证由主 Agent 负责。未运行桌面自动化、未修改真实素材或生产数据、未改变版本、未提交。
