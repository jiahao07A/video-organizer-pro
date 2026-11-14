# R2 文件操作及BOM存储修复独立复审

Status: 通过；本修复范围未发现需继续派修的问题
Reviewer: review_storage（首审发现者，未编写文件操作与BOM修复）

## 实际审查

逐项检查 `_batch_rename()`、`_rollback_session()`、物理迁移收尾的实际diff和新 `test_review_file_operations.py`，并对照 `fix-file-operations.md`。另检查 `storage_backup.py` 与 `data_directory.py` 的两处 utf-8-sig 替换和完整 `test_review_bom_storage.py`。

- 回滚在任何物理操作前核对数据库目标和源路径冲突；视频/XMP/DB单步失败有补偿，补偿失败会保留历史并明确报错。
- DB更新提交后抛错按同入库编号的实际路径判断；历史逐条删除，使部分成功后重试不碰已完成记录或后来出现的外部文件。
- 历史删除失败且记录仍在时，会补偿DB与文件；历史删除实际提交后才抛错则可以正确视作成功。
- Windows批量改名保留规范化查询结果，不再以原字符串错误过滤；物理迁移只重映射成功项，失败/阻止项及原目录范围边界保持。
- BOM只改变配置读取解码，原文件及备份快照字节、SHA-256完整性验证、非法JSON/非对象拒绝与失败不激活行为不变。

## 独立验证

```powershell
rtk proxy python -X utf8 -m pytest tests/test_review_file_operations.py tests/test_review_bom_storage.py tests/test_backup_restore.py tests/test_data_directory.py tests/test_real_workflow_regressions.py tests/test_physical_migration.py tests/test_post_review_hardening.py tests/test_work_scope.py -q -rs
```

结果：**107 passed in 40.94s**，exit=0，没有skip。该次覆盖真实SQLite和合成文件的补偿/重试、部分历史清除、外部冲突、Windows等价路径、迁移范围、BOM备份恢复重启与搬迁、WAL快照及恢复失败/中断恢复等既有回归。

`rtk proxy python -X utf8 -m ruff check tests/test_review_file_operations.py tests/test_review_bom_storage.py --select F401,F811,F821`：All checks passed。

额外独立探针：`rtk proxy python -X utf8 .scratch/review-cycle-20261114/storage_r2_probe.py`，四项断言全部通过，exit=0：

1. 历史删除已提交再抛错：视频、XMP、DB均恢复，历史为空，报告成功。
2. 路径更新/补偿均提交后抛错，同时历史删除失败：正确回到改名后路径、保留可重试历史，恢复故障后重试成功。
3. 外部视频占据原路径：原视频与外部文件字节均保留，DB仍指改名后路径，不覆盖。
4. **真实Windows共享占用**：通过 `CreateFileW` 以共享读写但不共享删除的方式持有新视频，真实 `os.rename` 返回 WinError32；回滚不移动视频/XMP、不变更DB、历史保留，CloseHandle后重试成功。这一项没有mock文件重命名。

## 边界

本机Windows11。上述占用验证不是Windows10实机证据，也不覆盖跨盘/网络盘/所有ACL/断电。文件系统与SQLite仍不是同一事务；持续故障导致补偿不可完成时只能明确保留现场并人工处理，不能声称任意故障均自动恢复。

FCPX输出故障由单独导出修复处理，未将其混为本组新回归。服务诊断的独立单行修改也不属于本组审查结论。本轮只新增scratch报告和探针，无产品/测试修改、commit、用户素材访问或目录删除。
