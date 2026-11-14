# GUI R3：素材库自身执行任务与服务锁门禁修复

Status: 修复及定向验证完成，等待 review_watch 独立复审

## 问题及实现

独立复审发现：取消入库虽然检查工作台任务，但素材库自身恢复线程运行时仍可删除其操作中的SQLite记录。本执行包保持现有快照与确认前后两次检查，仅扩展 `MaterialLibraryView._reject_workstation_busy()`：

- 服务 `_analysis_run_lock.locked()` 为真时拒绝写操作。
- 素材库自身 `worker`、`_io_worker`、`recovery_worker` 的 `isRunning()` 为真时拒绝；已销毁Qt包装对象视为不再运行。
- 保留同一顶层窗口工作台 `_has_active_background_task()` 检查。
- 不检查 `_detail_worker` / `_query_worker` 等只读查询，不误阻断其与取消入库并行。
- 提示统一为“已有后台任务在运行，请稍候再…”。

取消入库确认前/Yes后既有门禁调用、一次冻结的目标快照和累加入口共享同一门禁均保留，未改其他业务或标签页。

## 红绿证据

新增真实QThread场景后先运行 `tests/test_review_gui_uncatalog_gate.py -q -x`：旧8项通过，素材库自身 `worker` 活动时记录仍被删除的测试失败（1 failed, 8 passed），确认原问题。

修复后新增14项回归：素材库3个执行角色 × 确认前/确认期间/累加入口共9项、真实服务锁3项、只读线程不误拦截2项。活动场景断言SQLite记录完整、范围不变、原线程引用保留、没有追加扫描，空闲取消入库和确认拒绝语义继续通过。

```powershell
rtk proxy python -X utf8 -m pytest tests/test_review_gui_uncatalog_gate.py tests/test_review_recovery_busy_gate.py tests/test_review_gui_actions.py tests/test_ops_filter_scope.py -q -rs
```

结果：100 passed in 81.38s，无跳过、无告警。

`rtk git diff --check` 通过。新测试模块 `ruff --select F401,F811,F821` 通过。扩大检查产品文件时发现4个未使用导入：`QHBoxLayout`（此前换行布局后不再用）、`COL_LIBRARY_ID`、`COL_LIST_NO`、`SettingsManager`。因本执行包禁止修改门禁以外位置，未自行清理，已通知主Agent统一处理；未把该产品静态检查报告为通过。

## 范围与交接

本轮产品只改 `gui/views/material_library.py::_reject_workstation_busy`，测试只扩展 `tests/test_review_gui_uncatalog_gate.py`。没有修改 service、tags_library、布局或其他Agent变更，未提交、未spawn、未删除目录、未主动运行全量套件。编辑权已释放，可由 review_watch 直接复审。

测试使用本机Windows 11离屏真实Qt线程/SQLite与合成文件，不宣称Windows 10、可见GUI、公网分析任务稳定性通过。

### 后续授权：移除本轮新增的未使用导入

主Agent明确授权仅移除此前布局修改引入的未使用 `QHBoxLayout`，已完成；另外3项既有未使用导入保留为审计项，不混入本修复。导入调整不改变运行行为，编辑权再次释放。
