# 文件操作三项修复与验证

Status: 修复及定向回归完成，待主 Agent 独立复审
Branch: fix/review-hardening-20261114

## 对照执行包

修改范围仅 `core/video_organizer_service.py` 的 `_batch_rename()`、`_rollback_session()`、`execute_physical_migration()`，新增 `tests/test_review_file_operations.py`。未触碰 FCPX XML、BOM、诊断或其他代理职责符号，无 commit。

- 回滚先核对历史目标在数据库存在，再进入处理门禁和文件操作。目标视频、源视频或源侧车有冲突时明确拒绝，不覆盖。
- XMP 必须与视频一并恢复，否则补偿视频使整项仍可重试。数据库写入失败时反向补偿视频和已移动侧车；以同一入库编号的实际数据库路径判定事务是否已经提交，避免提交后抛错导致盲目补偿。
- 成功回滚逐条清理对应历史，部分失败后下一次只重试未完成项，不用“旧路径存在”推断已恢复，也不处理成功项新路径后来出现的外部文件。
- 历史清理失败时补偿数据库、侧车和视频并保留失败历史；补偿本身失败时报告具体阶段、保留文件和历史，明确需要人工核对。
- 物理整理只将结果为 `success`/`success_with_warning` 的映射交给既有 `_remap_work_scope()`；失败和阻止项保持原路径，目录范围不会扩张。
- 改名使用已经规范化查询的数据库目标集，移除原始字符串二次过滤，Windows 路径大小写/斜杠变体能够操作且不扩大到范围外素材。

## 回归证据

遵循 diagnosing-bugs 技能的失败回归、最小修复、重新验证流程；诊断依据主 Agent 已确认的探针及方案。

1. 新增最初 9 场景在修复前：**7 failed、2 passed**；明确捕获视频/XMP补偿、提交后抛错、部分回滚历史、等价路径与移动范围问题。
2. 最小修复后同批：**9 passed**。
3. 补充历史清理失败、补偿失败和外部目标不覆盖，最终新模块 **12 passed in 11.54s**。
4. 指定范围与监控回归：

```powershell
rtk proxy python -X utf8 -m pytest tests/test_review_file_operations.py tests/test_real_workflow_regressions.py tests/test_physical_migration.py tests/test_post_review_hardening.py tests/test_work_scope.py tests/test_media_watch_workspace.py -q -rs
```

结果 **90 passed in 94.98s**；该次包含新增模块前 11 项，最后补充的外部目标保护场景在最终 12 项模块单独通过。不重复相加作为唯一测试数量。

```powershell
rtk proxy python -X utf8 -m py_compile core/video_organizer_service.py tests/test_review_file_operations.py
rtk git diff --check
```

均通过。全部文件和 SQLite 为临时合成输入，无公网 AI、注册表写入、真实素材或用户数据操作。

## 风险与未验证项

文件系统与 SQLite 仍无法组成同一事务。正常可补偿失败保持可重试；故障持续到补偿阶段、数据库不可读或外部同时占据恢复路径时明确保留现场和失败历史，不能自动保证恢复。跨盘、网络共享、断电、杀进程及 Windows 10 实机未验证。已使用真实 Windows 11 文件和数据库故障注入，不据此声称全平台通过。

FCPX XML、UTF-8 BOM 修复按主 Agent 安排由下一代理串行进行。当前大服务文件编辑权已释放。
