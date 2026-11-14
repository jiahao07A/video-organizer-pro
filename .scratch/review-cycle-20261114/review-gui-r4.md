# GUI R4：素材库自身执行任务门禁独立复审

Status: 独立复审完成；R3确认问题已修复，未发现本次门禁修复新增可确认问题
Reviewer: R3原问题发现者；未实现此次门禁修复

## 审查范围与代码结论

在实现代理明确释放编辑后，读取 `MaterialLibraryView._reject_workstation_busy()`、取消入库两次门禁以及指定新增回归。当前门禁逐一检查服务 `_analysis_run_lock`、素材库自身 `worker`/`_io_worker`/`recovery_worker`，以及同顶级窗口工作台 `_has_active_background_task()`。已销毁Qt包装对象不误作正在运行；只读查询/详情角色不在执行角色中。

`uncatalog_selected()` 仍在确认前、确认Yes返回后分别检查同一门禁。目标路径列表只从同一个已解析快照复制，确认期间不重新读取选择；拒绝路径不调用取消入库服务、不改工作范围、不替换活动worker。

## 独立验证

```powershell
rtk proxy python -X utf8 -m pytest tests/test_review_gui_uncatalog_gate.py tests/test_review_gui_actions.py tests/test_review_recovery_busy_gate.py -q -rs
```

**93 passed in 41.31s**，无失败或跳过。包含：

- 工作台分析/IO/恢复真实QThread确认前与确认期间的跨页保护；
- 素材库自身三种执行角色确认前/确认期间保护，以及累加入工作范围保护；
- 服务分析锁确认前/确认期间阻止取消入库；
- 素材库只读查询/详情线程仍运行时，空闲取消入库可正常完成；
- 空闲Yes/No语义、原文件保留、中央恢复入口不覆盖worker、目标范围和退出线程检查。

```powershell
rtk proxy python -X utf8 -m pytest .scratch/review-cycle-20261114/gui_r2_probe.py -q -rs
```

原R2独立探针 **5 passed in 2.91s**。未把两次运行重复覆盖相加当作全量通过数。

### 原R3缺口的相同独立探针重跑

使用实际两页面、临时SQLite、合成文件；在 `library.recovery_worker` 挂真实仍运行的QThread，工作台空闲，调用真实取消入库入口。原R3得到 `database_record_preserved=false`，当前同场景结果：

```json
{"library_recovery_running":true,"database_record_preserved":true,"worker_reference_preserved":true,"busy_status_shown":true}
```

退出探针前释放并等待真实线程，未强制终止线程。

### 冻结目标与空闲路径独立探针

真实3条记录，确认前选择A，确认回调把当前选择改为C并返回Yes。仅A取消入库、B/C记录保留、工作范围仍保留原有显式路径且3个磁盘文件均保留。快照只解析1次：

```json
{"snapshot_once":true,"outside_records_preserved":true,"explicit_scope_unchanged":true,"all_source_files_preserved":true}
```

## 限制与工作区

验证基于当前Windows11上的离屏真实Qt对象/线程、临时SQLite与合成文件；不代表Windows10实机、可见桌面所有布局、收费供应商恢复请求或长期并发稳定性已实测。布局由另一代理独立审查，本报告不代替其结论。没有修改产品或tests，没有提交；只新增本报告。
