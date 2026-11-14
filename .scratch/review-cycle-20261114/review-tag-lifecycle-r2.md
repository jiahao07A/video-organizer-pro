# 标签统计刷新生命周期独立复审 R2

结论：本次定向复审未发现需要继续修复的确认问题，可以通过此修复包的复审。只读复审；未修改产品代码及现有测试，未提交 Git。

## 任务清单

- [x] 读取实际 diff、五场景回归测试及退出等待器实现。
- [x] 核对 Qt 对象归属、后台持有的引用、完成信号连接方式。
- [x] 运行五项生命周期测试及标签相关小范围测试。
- [x] 自建真实 SQLite、高频请求、排队信号销毁及 GC/运行中销毁探针。
- [x] 验证现有退出等待器可以发现 TagsView 的当前活跃 Python 线程。
- [x] 复跑人工退出和临时环境测试。

## 标准与实现检查

- `_usage_refresh_timer` 是 `QTimer(self)`，随页面销毁；single-shot 500ms 防抖取代无 owner 的延迟回调。
- `_UsageRefreshBridge` 在 GUI 线程创建、无 Qt parent；后台 target 为模块级函数，参数只有数据库和独立 bridge，没有闭包引用页面、没有从后台操作 QWidget。
- 完成连接显式使用 `Qt.QueuedConnection`，接收方法为 `@Slot(bool)`，数据模型更新回到 GUI 线程。
- 页面运行中销毁后，后台线程参数继续强引用 bridge，避免页面 Qt 对象生命周期提前销毁信号桥；页面销毁会自动断开到页面的连接。
- 活跃 `_usage_refresh_thread` 不会被覆盖，线程 finally 不清空页面属性；退出等待器可从窗口子 QWidget 属性读取该 Python 线程。
- 后台失败记录异常类型，不泄漏数据库异常中的路径或其他信息；成功信号只在刷新成功时更新 UI，后续允许重新刷新。

## 规格与行为验证

1. 原五项生命周期测试真实运行通过，覆盖：延迟前销毁、运行中销毁、重复请求合并、数据库异常恢复并用真实 SQLite 更新 UI、延迟前多次 load 合并。
2. 独立探针 `probe_tag_lifecycle_r2.py` 使用临时路径、真实 SQLite/PySide6，未访问用户数据库或素材：
   - 连续 400 次直接请求与 20 次 load，请求期间首个线程引用未改变。
   - 数据库统计总共 2 次、同时活跃峰值 1；真实 7 条素材的标签使用数回到页面模型为 7。
   - bridge/timer 归属 Qt GUI 线程；完成 slot 的实际 Python 线程 ID 等于 GUI 线程 ID。
   - 真实阻塞统计期间 `wait_for_background_work(root, timeout=0.05)` 返回 False；完成后返回 True。
   - 30 次先 join 工作线程、保留 queued 完成信号、真实 DeferredDelete 页面、再处理事件：删除后的回调次数为 0。
   - 15 次后台仍阻塞时删除页面、删除 Python 页面变量并 GC：弱引用观察 bridge 仍有效；释放工作线程后均能正常结束，删除后的回调次数为 0。
   - `threading.excepthook` 捕获异常 0；Qt 跨线程/对象已销毁相关警告 0。

## 命令与结果

- `rtk proxy python -X utf8 -m pytest tests/test_review_tag_refresh_lifecycle.py tests/test_tag_work_views.py tests/test_tag_group_ops.py tests/test_tag_config_groups.py tests/test_tag_group_heal.py -q`
  - 33 passed in 16.96s。
  - 21 条现有 `QSortFilterProxyModel.invalidateFilter()` 弃用告警，不影响本次生命周期结果。
- `rtk proxy python -X utf8 .scratch/review-cycle-20261114/probe_tag_lifecycle_r2.py`
  - exit=0，三个独立场景全部通过。
- `rtk proxy python -X utf8 -m pytest tests/test_review_manual_shutdown.py tests/test_manual_test_environment.py -q`
  - 10 passed in 6.21s。

## 证据边界

- 独立探针初次因过宽的 `Cannot` 告警筛选将 Qt 缺省字体目录警告误判为生命周期错误；调整为明确的跨线程/已销毁对象告警后重跑通过。未改产品逻辑和断言目标。
- 本次确认的是本机 Windows 上真实 Qt/SQLite 的生命周期和退出协作，不包含可见窗口人工操作、冻结包，也不增加 Windows 10 实机兼容性结论。
