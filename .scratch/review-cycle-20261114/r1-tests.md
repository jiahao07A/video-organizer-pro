# R1-E 全部测试有效性审计

Status: 首轮审计完成，等待主 Agent 复核及分派修复
Baseline: dd609f8
Branch: fix/review-hardening-20261114
环境：Windows 11 x64 / Python 3.13.12 / Qt 6.10.2 / pytest 9.1.1

## 结论与审计深度

当前 63 个 `test_*.py` 模块，678 个 `test_` 测试函数，2327 条 assert；pytest 参数展开后收集 919 个用例。完整 AST 索引保存于本目录 `test-inventory.json`，包括每个测试函数行号、断言和调用符号。所有模块已完成结构检查（测试入口、断言、raises、mock、skip、服务临时路径），并对下面三个候选规则进行了独立进程故障注入。没有进行全部 919 用例的穷举变异测试，不能宣称每条测试对所有可能错误均有效。

测试整体包含真实 SQLite、临时文件、真实 OpenCV/合成视频、回环 HTTP/OpenAI SDK、真实 Qt 事件循环与线程，并非全部用替身验证替身。旧审计中大范围空库、持久化可选断言、BGR入口绕过等问题当前已能看到针对性修正。本轮未复用历史通过数字。

## 已确认问题

### T1 / P2：别名及待审词排除测试没有放入需要排除的候选

定位：`tests/test_tag_vocab.py:107`，候选列表在 108–111，排除集合在 121–122；被测 `core/tag_vocab.py:106`。

输入标准词集合不含 `男人` 或 `赛博`，因此即使整个别名/待审过滤分支删除也不会触发断言。探针 `aliases` 在内存删除该分支，直接见证候选包含 `别名`、`待审`，随后 `test_tag_vocab.py` 与 `test_slim_analysis_fields.py` 仍 **18 passed**。

建议：候选本身包含别名、待审词以及合法标准词，逐组精确断言排除后的集合，并确认正常标准词保留；至少让两个排除来源分别能独立失败。此项为测试检测缺口，当前生产过滤逻辑在，未发现当前产品失效。

### T2 / P2：总词表预算断言从未触发总预算边界

定位：`tests/test_tag_vocab.py:107`，`per_closed_max=5`、`total_max=20` 在 123–124，断言在130；被测 `core/tag_vocab.py:94`。

当前样本按组裁剪后最多 5+2+1=8 词，低于20，所以总预算保护被完全删除也能通过。探针 `budget` 在内存将 `remaining_budget=total_max` 替换为10000，直接见证 `total_max=1` 却返回4词，同两模块仍 **18 passed**。

建议：构造多个各自不超过组上限、合计超过总预算的组，精确断言全部组总和及后续组分配，补充总预算0；保留按组上限及排序断言。此项为测试检测缺口，未确认当前产品预算失效。

### T3 / P2：XMP 用例只检查任意一个标签文本，没有验证实际标签列表完整

定位：`tests/test_analysis_tagging_bugs.py:29`，断言50使用“男性或室内或治愈”任一子串；生产 `core/video_organizer_service.py:6218`。

探针 `xmp` 仅在内存把 `dc:subject` 标签列表裁到第一项，导出可继续成功却丢失其余标签；`pytest tests -k xmp` 仍 **14 passed、905 deselected**。建议用 ElementTree 解析命名空间下 `dc:subject/rdf:Bag/rdf:li`，精确断言全部标签，并核对摘要、层级标签、特殊字符转义及另一路径之外素材未导出。此项为测试检测缺口；当前生产代码未做该裁剪。

### T4 / P2：FCPX 测试只查文件名，遗漏当前已经发生的 XML 无效输出

定位：`tests/test_post_review_hardening.py:116`，FCPX断言在138–141；生产 `core/video_organizer_service.py:5986` 附近。

当前生产代码直接插值文件名、分类、标签、摘要，没有XML转义。`fcpx` 探针用合法Windows文件名 `clip & quote.mp4`、分类 `A&B` 和摘要 `画面 <窗口> 与人物`，导出后 `ElementTree.parse` 直接失败：`not well-formed (invalid token): line 6, column 69`。因此本项同时包含确认的产品故障与测试缺口，已发送主 Agent 路由至存储/导出修复。

建议：修复导出器使用标准XML构造工具及标准文件URI，再加入解析导出内容的测试，验证名称、原路径、分类、完整标签、摘要可往返；是否能被真实Final Cut Pro导入还需该软件实机，XML可解析不能代替编辑软件兼容验收。

## 可复核命令与结果

全部在 `pwsh.exe`、项目根目录运行；探针只改变当前 Python 进程，不写回产品/测试源码，不调用公网AI。不计为完整套件重跑。

```powershell
rtk proxy python -X utf8 -m pytest --collect-only -q
# 919 tests collected
rtk proxy python -X utf8 .scratch/review-cycle-20261114/test_audit_probe.py inventory
# 63 modules / 678 test functions / 2327 asserts
rtk proxy python -X utf8 .scratch/review-cycle-20261114/test_audit_probe.py aliases
# 18 passed；故意放行别名与待审
rtk proxy python -X utf8 .scratch/review-cycle-20261114/test_audit_probe.py budget
# 18 passed；故意忽略总词数预算
rtk proxy python -X utf8 .scratch/review-cycle-20261114/test_audit_probe.py xmp
# 14 passed, 905 deselected；故意丢弃第二个及后续dc:subject标签
rtk proxy python -X utf8 .scratch/review-cycle-20261114/test_audit_probe.py fcpx
# CURRENT_PRODUCT_BUG: XML ParseError
```

进程预先导入核心模块后调用 pytest 会有1条 `PytestAssertRewriteWarning: anyio`，不是产品警告或跳过用例。探针最初尝试修改包装函数，定位出实际实现 `_sync_metadata_to_xmp` 后完成正确注入；最终脚本使用AST提取函数，源码始终未修改。

## 其他检查与边界

- 未发现 `or True` 恒真断言。两个无 assert 的测试是明确“调用不抛异常”的空列表/已退出启动冒烟，不能单凭无assert判定无效。
- 所有服务实例显式临时DB，或测试通过临时DataDirectoryManager/根路径替换定位；3个未传db_path的调用均为数据目录/启动门禁的有意场景。未发现本轮证据支持其读取真实素材库。
- 参数化压力模块5个函数展开9个场景默认跳过，显式压力执行由主 Agent 负责。默认绿不能表示压力验证通过。
- Windows路径和进程窗口标志有真实平台条件；没有Windows10环境时不能把Windows11中的 `os.name == 'nt'` 通过冒充Windows10验证。
- `test_tag_ai_worker.py` 明确只调用同线程 `run()`，验证成功/错误信号，不能独立证明UI不阻塞；真实异步交互仍需主 Agent GUI实测与相关GUI模块审查。此项为覆盖范围说明，未另计确认bug。
- GUI测试为离屏，不能验证真实DPI、多显示器、所有窗口尺寸和用户操作手感；真实剪辑软件导入、公网供应商稳定性同样不在当前自动化测试证据内。
- 没有修改产品、现有tests、凭据、用户数据，没有commit，没有删除目录。临时探针输出保留在系统临时目录，供主 Agent 验证；报告不包含API凭据或真实素材。

## 全部测试模块覆盖索引

下表“结构审查”表示已纳入AST断言/替身/skip/隔离检查与测试入口清点，不表示每个测试做过故障注入。T1/T2/T3/T4的深度验证见上文；产品各模块的真实功能验证由其他职责报告和主 Agent完整回归共同提供。

| 模块 | 测试函数 | assert | raises | mock点 | skip点 | 本轮审计 |
|---|---:|---:|---:|---:|---:|---|
| test_ai_gateway.py | 19 | 62 | 1 | 9 | 0 | 结构审查 |
| test_analysis_concurrency.py | 1 | 3 | 0 | 4 | 0 | 结构审查 |
| test_analysis_failure_center.py | 34 | 122 | 2 | 31 | 0 | 结构审查 |
| test_analysis_flow_service.py | 9 | 32 | 0 | 16 | 0 | 结构审查 |
| test_analysis_job_metrics.py | 5 | 21 | 0 | 0 | 0 | 结构审查 |
| test_analysis_job_policy.py | 16 | 69 | 0 | 0 | 0 | 结构审查 |
| test_analysis_orchestration_retry.py | 7 | 20 | 0 | 27 | 0 | 结构审查 |
| test_analysis_result_validation.py | 29 | 104 | 0 | 8 | 0 | 结构审查 |
| test_analysis_stress.py | 5 | 22 | 0 | 4 | 1 | 结构审查；9压力场景需显式开启 |
| test_analysis_tagging_bugs.py | 5 | 16 | 0 | 8 | 0 | 结构审查；T3故障注入漏检 |
| test_analysis_targets.py | 8 | 14 | 0 | 0 | 1 | 结构审查 |
| test_analyze_operation_pipeline.py | 3 | 6 | 0 | 0 | 0 | 结构审查 |
| test_audio_transcriber.py | 3 | 17 | 0 | 3 | 0 | 结构审查 |
| test_backup_restore.py | 8 | 38 | 0 | 5 | 0 | 结构审查 |
| test_build.py | 3 | 9 | 2 | 3 | 0 | 结构审查 |
| test_cold_start_ai.py | 4 | 14 | 0 | 2 | 0 | 结构审查 |
| test_column_prefs_migrate.py | 3 | 8 | 0 | 0 | 0 | 结构审查 |
| test_data_directory.py | 29 | 87 | 11 | 20 | 0 | 结构审查 |
| test_data_directory_gui.py | 6 | 13 | 0 | 1 | 3 | 结构审查 |
| test_diagnostic_feedback.py | 38 | 116 | 1 | 16 | 2 | 结构审查 |
| test_frame_color_bgr.py | 3 | 10 | 0 | 1 | 0 | 结构审查 |
| test_gui_review_regressions.py | 34 | 117 | 0 | 24 | 3 | 结构审查 |
| test_http400_recovery.py | 25 | 92 | 3 | 6 | 0 | 结构审查 |
| test_layout_search_fixes.py | 7 | 25 | 0 | 0 | 0 | 结构审查 |
| test_list_numbers_refresh.py | 2 | 4 | 0 | 0 | 0 | 结构审查 |
| test_manual_test_environment.py | 3 | 30 | 0 | 0 | 0 | 结构审查 |
| test_material_display_fixes.py | 7 | 22 | 0 | 3 | 0 | 结构审查 |
| test_media_registry.py | 8 | 28 | 0 | 1 | 1 | 结构审查 |
| test_media_watch.py | 17 | 55 | 0 | 1 | 0 | 结构审查 |
| test_media_watch_workspace.py | 30 | 132 | 1 | 39 | 0 | 结构审查 |
| test_model_providers.py | 14 | 54 | 2 | 1 | 0 | 结构审查 |
| test_model_selection.py | 8 | 35 | 2 | 6 | 0 | 结构审查 |
| test_operation_targets.py | 12 | 26 | 0 | 0 | 0 | 结构审查 |
| test_ops_filter_scope.py | 7 | 25 | 0 | 0 | 0 | 结构审查 |
| test_organization_plan.py | 6 | 23 | 2 | 0 | 0 | 结构审查 |
| test_organization_plan_gui.py | 3 | 17 | 0 | 0 | 1 | 结构审查 |
| test_physical_migration.py | 2 | 9 | 0 | 0 | 0 | 结构审查 |
| test_post_review_hardening.py | 19 | 84 | 0 | 18 | 0 | 结构审查；T4真实导出故障 |
| test_real_workflow_regressions.py | 9 | 18 | 0 | 2 | 0 | 结构审查 |
| test_recovery_controls.py | 23 | 90 | 1 | 15 | 0 | 结构审查 |
| test_runtime_performance.py | 17 | 49 | 6 | 23 | 1 | 结构审查 |
| test_settings_catalog.py | 13 | 29 | 0 | 5 | 1 | 结构审查 |
| test_settings_persist.py | 13 | 41 | 1 | 5 | 0 | 结构审查 |
| test_slim_analysis_fields.py | 5 | 19 | 0 | 0 | 0 | 结构审查；参与T1/T2故障注入 |
| test_smoke_exit.py | 3 | 5 | 1 | 3 | 0 | 结构审查 |
| test_software_update.py | 20 | 30 | 17 | 25 | 0 | 结构审查 |
| test_software_update_gui.py | 13 | 40 | 0 | 20 | 0 | 结构审查 |
| test_startup_upgrade.py | 16 | 62 | 2 | 7 | 0 | 结构审查 |
| test_startup_upgrade_gui.py | 1 | 7 | 0 | 2 | 1 | 结构审查 |
| test_tag_ai_assist.py | 13 | 43 | 0 | 0 | 0 | 结构审查 |
| test_tag_ai_worker.py | 2 | 4 | 0 | 0 | 0 | 结构审查；仅同线程信号覆盖 |
| test_tag_config_groups.py | 2 | 11 | 0 | 3 | 0 | 结构审查 |
| test_tag_group_heal.py | 2 | 6 | 0 | 0 | 0 | 结构审查 |
| test_tag_group_ops.py | 15 | 68 | 0 | 7 | 0 | 结构审查 |
| test_tag_import_service.py | 5 | 11 | 0 | 0 | 0 | 结构审查 |
| test_tag_normalize.py | 11 | 32 | 0 | 0 | 0 | 结构审查 |
| test_tag_vocab.py | 13 | 60 | 0 | 0 | 2 | 结构审查；T1/T2故障注入漏检 |
| test_tag_work_views.py | 9 | 41 | 0 | 0 | 0 | 结构审查 |
| test_update_mirror.py | 8 | 14 | 1 | 12 | 0 | 结构审查 |
| test_verify_windows_qt_bundle.py | 2 | 5 | 1 | 0 | 0 | 结构审查 |
| test_video_catalog.py | 6 | 16 | 0 | 0 | 0 | 结构审查 |
| test_video_memory_cache.py | 2 | 7 | 0 | 0 | 0 | 结构审查 |
| test_work_scope.py | 13 | 38 | 0 | 0 | 0 | 结构审查 |
