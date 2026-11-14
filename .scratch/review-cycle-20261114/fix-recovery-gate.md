# R2-G2：中央手动恢复入口的跨页忙碌门禁

Status: 修复及相关组合回归完成，等待独立复审

## 实现

仅修改 `gui/widgets/recovery_controls.py` 的 `launch_manual_recovery()` 及新增私有 `_manual_recovery_busy()`。

门禁同时检查服务分析锁、发起页面自身执行任务，以及同一顶级窗口的工作台分析/IO/恢复任务；仅检查执行角色属性（`worker`、`_io_worker`、`recovery_worker`）及页面既有判断，不遍历所有 QThread，因此不会把只读目录分页/详情查询误作执行任务。进入确认窗口之前与 `ManualRecoveryDialog.Accepted` 返回后各检查一次，忙碌时不构造新 AnalysisWorker，不覆盖 `worker/recovery_worker`。

空闲确认仍按原目标与策略启动一次，取消不启动。本修复不改变重试策略和恢复预算。

## 新增验证

```powershell
rtk proxy python -X utf8 -m pytest tests/test_review_recovery_busy_gate.py -q -rs
# 20 passed in 19.70s
```

测试使用临时 SQLite、同一顶层 QWidget 下的真实工作台/素材库和真实运行中的 QThread。包括两页面×三种工作台线程×确认前/确认期间，共十二场景；另覆盖素材库自身三种线程、确认前/后服务分析锁、空闲确认与取消、只读查询线程不阻塞恢复。仅收费分析 Worker 与确认窗口使用计数替身，不发送外网请求；断言线程继续存活且引用保留、数据库记录和范围不变、新 Worker 构造数为零。

原 R2 独立红色探针复跑：

```powershell
rtk proxy python -X utf8 -m pytest .scratch/review-cycle-20261114/gui_r2_probe.py -q -rs
# 5 passed in 5.10s（原2 failed/3 passed现均通过）
```

`git diff --check`、修改文件编译与新增测试 `ruff --select F401,F811,F821` 通过。

相关组合：`tests/test_recovery_controls.py tests/test_http400_recovery.py tests/test_gui_review_regressions.py tests/test_review_gui_actions.py tests/test_review_gui_uncatalog_gate.py`，**161 passed in 172.69s**，无跳过。

未修改服务、工作台、素材库布局、原测试；未 commit、删除目录或访问真实素材/凭据。Windows 10/可见桌面、公网供应商验证仍由主 Agent 汇总边界。
