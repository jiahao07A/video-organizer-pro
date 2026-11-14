# R1-C 前端交互与生命周期审查

Status: 首轮只读审查完成，发现 5 个可复现问题
Baseline: dd609f8
环境：Windows 11 x64，Python 3.13.12，PySide6 6.10.2，Qt offscreen。

## 已确认问题

|级别|位置|触发及影响|直接证据|
|---|---|---|---|
|P1|`gui/views/workstation.py:1296`、`:1419`、`:1106`|工作台任务运行时仅禁用工具栏，表格及右键菜单仍可用；右键“分析选中项”不检查活动任务，直接覆盖 `self.worker`。原任务失去本页取消目标，新任务可能重复排队，进度/完成回调也会互相解锁界面；分析线程没有 Qt parent，存在生命周期风险。|探针设置已活动 worker、`set_ui_enabled(False)` 后调用真实 `analyze_selected()`，输出 `ACTIVE_WORKER_REPLACED=True TABLE_ENABLED=True`。安全使用 stub 证明入口绕过，未强行制造进程崩溃。|
|P2|`gui/views/workstation.py:1296`|右键分析绕过主按钮的自动 XMP 副作用确认。自动 XMP 已开启但未强制重新分析时，点击右键分析直接启动；已有同名侧车可能被覆盖，用户没有该次操作确认。|真实入口探针输出 `XMP_PROMPTS=0 WORKERS_CREATED=1`；主按钮已有测试要求确认同名侧车覆盖。|
|P2|`gui/widgets/detail_panel.py:488`、`:511`|取消“合并标签”，删除全部标签并保存，`if new_tags` 跳过空列表写入；界面提示保存成功，数据库仍保留旧标签。单选及批量两条分支均同样判断。|真实临时 SQLite，`DetailPanel.load_video_data()`→取消合并→`set_tags([])`→保存按钮 `click()`，读取数据库仍为 `['old-tag']`。|
|P2|`gui/views/material_library.py:597`、`:606`|工作台任务运行时从素材库“累加到工作范围”仍立即调用 `append_work_scope(scan=True)`，跨页绕过范围冻结并在 GUI 线程扫描。分析/扫盘中的范围仍可改变。|用共享真实服务的工作台和素材库，工作台活动 worker 并锁 UI 后调用素材库实际入口，输出 `SCOPE_CHANGED_WHILE_BUSY=True ADD_BUTTON_ENABLED=True`。使用真实合成文件，仅抽帧替身。|
|P2|`core/manual_test_environment.py:438`、`:443`|人工验收退出等待把 `seen_threads` 放在轮询外，首轮等待 100ms 后永久跳过同一 QThread，第二轮便可能报告全部结束。随后清理临时数据或销毁 GUI 时线程仍在运行。|真实 QThread 用 Event 保持运行；`timeout=0.4` 时函数在 0.123 秒返回 True，`worker.isRunning()` 同时为 True。探针 finally 正常释放并等待线程，无后台遗留。|

所有复现均在 `.scratch/review-cycle-20261114/gui_probe.py`。命令：

```powershell
rtk proxy python -X utf8 -m pytest .scratch/review-cycle-20261114/gui_probe.py -q -s
```

前三项组合运行 3 failed；人工等待与跨页各自运行分别 1 failed，符合待修缺陷的预期。报告中的 P1 生命周期影响属于风险判断，已直接证实的是忙碌门禁失效和 worker 覆盖，未声称已复现崩溃。

## 已运行定向验证

- `rtk proxy python -X utf8 -m pytest tests/test_gui_review_regressions.py tests/test_ops_filter_scope.py tests/test_settings_persist.py tests/test_tag_work_views.py tests/test_layout_search_fixes.py tests/test_smoke_exit.py -q -rs`：92 passed，64.61 秒。
- `rtk proxy python -X utf8 -m pytest tests/test_material_display_fixes.py tests/test_data_directory_gui.py tests/test_startup_upgrade_gui.py tests/test_model_selection.py tests/test_tag_ai_worker.py tests/test_recovery_controls.py -q -rs`：66 passed，28.85 秒。
- `rtk proxy python -X utf8 -m pytest tests/test_operation_targets.py tests/test_work_scope.py tests/test_organization_plan_gui.py tests/test_tag_group_ops.py tests/test_tag_config_groups.py tests/test_settings_catalog.py tests/test_software_update_gui.py -q -rs`：76 passed，28.80 秒。

以上 19 模块共 234 项既有测试均通过，没有跳过。新探针揭示其未覆盖的真实入口差异，因此不能用 234 passed 推断所有交互可用。

## 审查覆盖

阅读主窗导航/懒加载/托盘/退出/更新互斥、工作台目标快照/分页/筛选/分析/重命名/导出/移动/范围按钮、素材库累加/取消入库/详情、详情编辑/推荐/故事板、标签待审/AI建议人工确认、设置应用/保存、模型列表选择器、多媒体组件与 worker 辅助实现。
定向测试直接覆盖主窗页创建、工作范围与筛选边界、模型选择与设置落盘、GUI移动/重命名预览、恢复入口、升级窗口及主要生命周期门禁。AI、导出底层和系统安装由其他职责代理负责；本代理没有把这些测试作为公网AI、原生播放器、真实安装结果的证明。

## 未验证与约束

- 本代理只做 Qt 离屏，未验证可见桌面点击手感、Windows 10、DPI缩放、多显示器、所有媒体编解码与音频设备。
- 未逐个手点每个菜单/文件选择框；主 Agent 负责可见 GUI 验收。
- 不修改产品或现有测试，不提交，不读取真实配置/凭据/用户素材；只增加本报告和隔离探针。
- 最初跨页探针使用 MainWindow 退出时触发忙碌警告，已中止该独立探针进程，最终改用同一顶级 QWidget 下两个真实页面证明跨页入口；没有将被中止运行计为测试通过。

## 建议由主 Agent 裁决的修复包

1. 将工作台右键和主按钮分析入口共用目标已冻结的启动流程与副作用确认，并在所有可写入口及确认对话框返回后复查忙碌门禁；覆盖已有分析/IO/恢复任务，防止重复 worker。
2. 素材库累加入口检查同一主窗工作台的活动任务，避免跨页改范围；有条件将扫描后台化，但不要因此扩大本轮范围。
3. 详情保存时区分“合并空列表=不改变”与“覆盖空列表=清空”，验证单选/批量真实数据库持久结果。
4. 人工等待每次轮询重建去重集合，补长于单轮100ms的真实线程超时/结束场景。

以上仅修复建议，未执行产品修改，等待主 Agent 确定文件责任并派发。
