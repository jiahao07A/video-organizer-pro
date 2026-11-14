# 工作台跨页执行门禁修复

状态：修复及定向测试完成，等待 review_gui 独立复审；已释放编辑权。沿用父议题、fix/review-hardening-20261114 分支与本轮审查基线。未 commit、spawn、删除或修改他人文件。

## 范围与清单

- [x] 真实 MainWindow、SQLite、IoWorker、临时 XMP 复现反向门禁遗漏。
- [x] 只扩展 `gui/views/workstation.py::_has_active_background_task`。
- [x] 验证分析锁、素材库三类执行任务及确认期间占用。
- [x] 验证只读线程不误拦、已删 Qt 包装忽略、空闲正常写盘。
- [x] 指定联合 GUI/响应布局回归完成，保存 JUnit。

新增测试：`tests/test_review_workstation_cross_page_gate.py`。未修改 service 写盘实现、现有确认后复查、冻结目标规则及其他测试。workstation.py 中本轮其他代理已有差异保持原样。

## 修改

统一忙碌判断先检查服务 `_analysis_run_lock.locked()`，再直接检查工作台及同窗 `library_page` 的 `_io_worker`、`worker`、`recovery_worker`。不调用另一页 checker，避免相互递归。已销毁的 Qt worker 包装仍按原规则忽略；`_detail_worker`、`_query_worker` 只读任务不纳入门禁。

因此已调用 `_reject_busy_operation` 的同步 XMP、应用重命名、物理整理等入口及其确认后的复查会识别素材库执行任务和服务层分析占用。

## 红绿证据

命令：`rtk proxy python -X utf8 -m pytest tests/test_review_workstation_cross_page_gate.py -q -rs`

- 红：新增第一组四个真实场景，服务锁单独占用、素材库三个执行角色各自活动时，旧实现都由真实工作台 IoWorker 覆盖 XMP sentinel，**4 failed in 9.52s**。失败均来自预期“XMP 应保持原字节”的业务断言。
- 绿：扩展统一判断后，同组 **4 passed in 2.46s**。
- 补齐确认期间启动/持锁、双页两种只读线程、空闲路径及已删 Qt worker 包装后，**14 passed in 17.12s**。

忙碌场景断言侧车原字节保持不变、没有启动新的工作台 IoWorker、原执行线程仍运行且引用不变；确认期间占用只显示一次原确认。另直接验证忙碌时应用重命名与物理整理入口不会继续解析操作目标。

空闲与只读场景执行原 `run_io_job`，实际启动 IoWorker、调用原 service 并写真实临时 XMP；ElementTree 解析结果，确认合成摘要及标签存在，排除通过一律拒绝操作制造“修复”。测试只替换自动更新、消息框响应并观察原 IO launcher，不替换忙碌线程、文件写入、DB 或业务目标解析。

## 最终验证

```text
rtk proxy python -X utf8 -m pytest tests/test_review_workstation_cross_page_gate.py tests/test_review_gui_actions.py tests/test_review_gui_uncatalog_gate.py tests/test_review_recovery_busy_gate.py tests/test_review_responsive_layout.py -q -rs --junitxml=.scratch/review-cycle-20261114/fix-workstation-cross-page-junit.xml
110 passed, 30 warnings in 99.62s (0:01:39)

rtk proxy python -X utf8 -m ruff check tests/test_review_workstation_cross_page_gate.py
All checks passed!

rtk git diff --check -- gui/views/workstation.py tests/test_review_workstation_cross_page_gate.py
通过
```

无失败、无跳过。30 条警告均为既有 `gui/models/tag_model.py:14` 的 invalidateFilter 弃用提示，未出现后台线程异常警告。

## 边界与交接

本机 Windows 11 x64、Python 3.13.12、PySide6 6.10.2，Qt 离屏真实事件循环；不作为 Windows 10 实机、可见桌面或长时间压力测试证据。使用临时 SQLite、合成文件和合成 XMP，无用户素材及公网 AI。修改范围是 GUI 已知执行任务的跨页门禁，没有新增 service 层原子锁架构。由未编写该修复的 review_gui 继续独立复审。
