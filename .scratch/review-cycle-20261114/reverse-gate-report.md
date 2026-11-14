# 工作台反向跨页门禁：真实写盘验证

日期：2026-11-14。范围：只读审查，未修改产品、既有测试或真实素材；仅新增本目录探针与报告。

## 已确认问题

**[P1] 素材库恢复/服务分析忙碌时，工作台仍可覆盖 XMP。**

同一真实 `MainWindow` 的素材库 `recovery_worker` 是活动 `QThread`，服务 `_analysis_run_lock` 保持锁定时，工作台 `sync_metadata()` 仍通过两次门禁、启动真实 `IoWorker`、调用真实 `VideoOrganizerService.sync_metadata_to_xmp()`，覆盖合成视频已有的合成 XMP。写入完成后，素材库恢复线程仍活动、分析锁仍锁定，界面还提示“已成功为 1 个视频生成 XMP 侧边文件”。

这不是门禁布尔检查或被替换服务的推断：使用真实临时 SQLite、真实页面和服务、真实 IO 线程、实际文件覆盖。替身仅用于隔离配置路径、自动更新检查与代替用户确认/消息框。

## 复现证据

命令（pwsh.exe）：

```powershell
rtk proxy python -X utf8 -m pytest .scratch/review-cycle-20261114/reverse-gate-probe.py -q -s --tb=short
```

结果：`3 passed in 2.72s`。注意探针断言用于**确认缺口复现**，不是期望产品行为的通过测试。

| 活动素材库恢复线程 | 服务分析锁持有 | 真实 IO Worker | 既有 XMP 被覆盖 | XMP 字节数 |
| --- | --- | --- | --- | --- |
| 是 | 否 | 是 | 是 | 1002 |
| 否 | 是 | 是 | 是 | 1002 |
| 是 | 是 | 是 | 是 | 1002 |

每次都确认写入 XMP 包含 `reverse gate synthetic summary`，SQLite 原素材仍存在，确认框恰好出现一次。所有任务在 finally 中正常释放并退出。没有可见 GUI、冻结包或公网请求。

## 原因与内部锁核查

- `gui/views/workstation.py:1433` 的 `_has_active_background_task()` 仅查询工作台自身 `_io_worker`、`worker`、`recovery_worker`，不查询服务 `_analysis_run_lock` 或素材库执行角色。
- `gui/views/workstation.py:1447` 的 `_reject_busy_operation()` 依赖该本页检查。
- `gui/views/workstation.py:1713` 的 XMP 入口确认前后使用同一门禁，因而二次检查也不能阻止此跨页场景。
- `core/video_organizer_service.py:6115` 的实际 XMP 写入仅持有 `_file_operation_lock`；它与 `_analysis_run_lock` 是两个锁。探针已证明内部服务未阻止实际写盘。
- 另外静态核查：`batch_rename()` 同样仅持有 `_file_operation_lock`；`execute_physical_migration()` 未检查 `_analysis_run_lock`。本次**未**用真实文件操作复现重命名/物理整理，因此不将两者静态推断当作动态实测结果。

## 交接

已向主代理报告。按任务约束停在证据，不自行修改产品或扩展修复范围。主代理决定是否将工作台门禁扩展到服务分析锁及素材库执行线程，并规划相应回归测试与独立复审。
