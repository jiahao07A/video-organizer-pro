# R1-A 数据存储与文件操作审查

Status: 审查完成；5项确认问题待主 Agent 分派
Baseline: dd609f8
Environment: Windows 11 x64、Python 3.13.12

## 已确认问题

1. **P1：重命名回滚遇数据库写入错误会留下磁盘与素材库不一致，并无法重试。** `core/video_organizer_service.py:5919` 先把 `new_path` 改回 `old_path`，之后 `:5936` 更新数据库；失败分支只报告而没有补偿文件操作。探针先真实改名，再仅将 `db.update_video_path` 注入 OSError：返回 False、旧文件存在、新文件不存在，但数据库仍只存新路径、工作范围仍指向新路径。取消注入后重试仍 False（目标文件不存在）。应在统一文件操作边界中保证DB与文件失败补偿，并针对部分回滚保留可重试历史。
2. **P2：FCPX XML 导出未转义用户内容，合法素材名即可产生损坏 XML。** `core/video_organizer_service.py:5989` 附近直接插入文件名、路径、分类、标签、摘要。真实 Windows 文件 `R&D.mp4`，摘要 `甲 & 乙 <镜头>`，导出提示成功，但 ElementTree.parse 报 `not well-formed (invalid token): line 6, column 70`。XMP同输入导出可正常解析。还需由主 Agent 安排真实 FCPX DTD/剪辑软件导入校验，XML语法通过不能证明FCPX格式可导入。
3. **P2：物理整理后没有同步显式工作范围。** `core/video_organizer_service.py:6665` 收尾只失效缓存；与重命名不同，没有调用范围重映射。真实源文件显式加入范围后成功移动，目标存在、DB路径已更新，`get_work_scope_paths()` 却仍是旧文件路径，`get_work_scope_video_paths()` 变成空列表。GUI `workstation.py:1796` 只重新 load_data，未补同步，因此完成后素材从当前工作台消失。只应重映射成功项，不扩大原目录范围。
4. **P2：受支持的 UTF-8 BOM 设置文件阻断备份。** `core/storage_backup.py:303` `_read_json_object` 使用 utf-8，普通设置加载器使用 utf-8-sig。合成 settings.json 用 utf-8-sig 写入后 `SettingsManager.load_settings` 成功，`backup_configuration()` 却返回空字符串，错误为 Unexpected UTF-8 BOM。管理目录搬迁同样依赖该备份且 `data_directory.py` 的复制后配置验证也使用 utf-8，需统一支持约定的配置编码。此问题可能阻断依赖备份的软件更新。
5. **P2：Windows 等价路径传给批量改名被静默忽略。** `core/video_organizer_service.py:5738-5739` 用选入路径的精确字符串再次过滤已按规范化路径查出的DB记录。真实 `MixedCase.mp4` 的大小写变体加正斜杠路径在Windows上 exists=True，传入 batch_rename 后源仍存在、目标未创建，报告 planned=0/success=0/failed=0。应按现有 normalize_work_path/path_status_key 规则比较或使用规范化查询返回的记录路径。

## 验证命令与结果

- `rtk proxy python -X utf8 -m pytest tests/test_backup_restore.py tests/test_data_directory.py tests/test_startup_upgrade.py tests/test_physical_migration.py tests/test_organization_plan.py tests/test_operation_targets.py tests/test_video_catalog.py tests/test_real_workflow_regressions.py -q -rs`：**93 passed in 34.98s**。
- `rtk proxy python -X utf8 -m pytest tests/test_post_review_hardening.py tests/test_work_scope.py tests/test_ops_filter_scope.py -q -rs`：**40 passed in 26.68s**。
- `rtk proxy python -X utf8 .scratch/review-cycle-20261114/storage_probe.py`：进程 exit=0，逐项输出上述5个缺陷的实际状态；该探针故意记录现状而非断言通过，不能把 exit=0 当作业务通过。结果保存在 `storage-probe-results.json`。

## 覆盖与限制

- 现有测试直接覆盖真实 SQLite/临时文件的 WAL 快照、备份校验、配置损坏、恢复失败回滚、中断恢复、目录激活、改名回滚、物理整理伪造计划和DB失败补偿、大范围查询及范围外排除。
- 新探针真实执行改名、回滚、移动、XMP/ALE/FCPX导出，数据库与文件为合成输入；只有回滚DB写失败使用故障注入。未使用用户素材或凭据，未访问公网。
- Windows 10、跨磁盘、网络共享、受限ACL、完整磁盘、杀进程断电、剪辑软件实际导入没有实机证据，不能宣称通过。
- 未修改产品或现有测试。仅新增本报告、隔离探针及JSON结果；未commit、删除目录、发外部消息或再调用subagent。探针临时合成目录保留供复核。
