# GUI R3：取消入库与中央恢复入口独立复审

Status: 已修复跨页场景验证通过；另确认1项邻接P2门禁遗漏，需继续修复
Reviewer: 未参与本轮GUI门禁实现

## 已修复场景与验证

核对 `fix-gui-uncatalog.md`、`fix-recovery-gate.md`、两处产品diff及新增测试。

- `MaterialLibraryView.uncatalog_selected()` 在确认前、Yes返回后均调用工作台忙碌门禁。路径列表由同一个快照复制，确认后不重新解析目标。
- `_manual_recovery_busy()` 同时检查服务分析锁、发起页面及同一顶层窗口的工作台执行角色，`launch_manual_recovery()` 确认前后分别检查。拒绝分支均在构造新 AnalysisWorker 和修改页面worker引用之前返回。
- 恢复入口没有遍历所有QThread，保持只读分页线程可并行，不将其误判为分析执行任务。

```powershell
rtk proxy python -X utf8 -m pytest tests/test_review_gui_uncatalog_gate.py tests/test_review_recovery_busy_gate.py .scratch/review-cycle-20261114/gui_r2_probe.py -q -rs
```

独立重跑 **33 passed in 28.47s**，无跳过；包括新增8+20项及此前2红3绿的原5项探针，现在原5项均绿。拒绝时数据库记录、范围和原线程引用均保留。

另以真实临时SQLite、实际两页面、合成3文件执行冻结目标探针：在取消入库确认中把选择从A换为C，实际只删除A且B/C保留；恢复确认中从B换为C，创建的替身分析worker只接收到B。两处快照解析次数均为1。输出：

```json
{"uncatalog_snapshot_resolved_once":true,"uncatalog_outside_record_kept":true,"recovery_snapshot_resolved_once":true,"recovery_original_target_only":true}
```

## 新确认P2：取消入库仍绕过素材库自身恢复任务

- 位置：`gui/views/material_library.py::_reject_workstation_busy()`（约602行），仅检查 `window().workstation_page._has_active_background_task()`；`uncatalog_selected()` 两次调用同一不完整门禁。
- 触发：从素材库启动手动400恢复，真实恢复线程属于 `library.recovery_worker`，而非工作台。素材库没有 `set_ui_enabled()`，取消入库按钮仍启用。此时点击取消入库并确认。
- 实际：取消入库删除仍被恢复任务使用的数据库记录，可能导致正在执行的分析写入重新建记录或与用户取消入库动作冲突。跨页工作台场景虽已修复，同页恢复仍未保护。
- 独立直接证据：创建真实 `WorkstationView`/`MaterialLibraryView` 与临时SQLite；在 `library.recovery_worker` 挂真实运行中的QThread，工作台没有执行任务；将确认替身返回Yes，调用真实 `uncatalog_selected()`。线程仍运行、按钮可用，数据库记录已删除：

```json
{"library_recovery_running":true,"delete_button_enabled":true,"database_record_preserved":false}
```

- 最小复现步骤：临时库放一条合成记录；创建同顶层两页面；启动 `BusyThread(library)` 并赋给 `library.recovery_worker`；断言 `isRunning()` 后 `QMessageBox.question` 返回Yes；执行 `library.uncatalog_selected()`；查询记录返回None。最后释放并wait线程。本审查实际执行该过程，没有通过静态推断冒充实测。
- 建议下一执行包：同一写操作门禁检查发起素材库自身 `worker`/`_io_worker`/`recovery_worker`，并检查服务分析运行锁，继续保留工作台检查和两次门禁。新增真实QThread×确认前/确认期间自身任务回归，确认拒绝时DB/范围/worker均保持；恢复空闲确认和原文件保留语义不变。

## 边界

仅只读审查与临时合成探针，未修改产品、现有或新增测试，无commit。上述GUI回归为离屏真实Qt对象/线程与SQLite；不代表Windows10实机、可见交互或公网恢复请求已验证。布局由另一独立代理负责，不在本报告裁决范围。
