# R2 GUI 修复独立复审

Status: 部分通过；发现2个仍需主 Agent 路由的门禁缺口
审查者：未参与本轮GUI产品修复的测试审计代理

## 已复核修复

只读检查 `gui/views/workstation.py`、`gui/views/material_library.py`、`gui/widgets/detail_panel.py`、`core/manual_test_environment.py` 的当前差异，以及 `tests/test_review_gui_actions.py`。

- 右键分析和主按钮共用 `_confirm_and_start_analysis`，初始及确认后检查活动分析、IO、恢复worker。右键先检查非空选中再进行工作范围交集，不回退成可见全部。独立负例确认选中范围外项只产生空目标，没有扩大；确认期间把选中从A换到B，创建Worker仍收到原A目标。
- force及自动XMP确认共用一份流程，明确显示覆盖副作用；现有新增测试覆盖两入口的3种组合以及拒绝不启动。
- 素材库累加在工作台busy时返回；空闲先 `append_work_scope(scan=False)`，再交给工作台真实 `IoWorker` 后台扫描。新增测试确实用事件阻塞真实后台线程，并检查GUI调用及时返回，非仅替身断言。
- 详情保存的单选/批量分支可在覆盖模式写入空tags；合并模式空tags保留原内容。新增测试通过真实临时SQLite核对。
- 等待函数每轮重置 `seen_threads`，修复首轮100ms后永久跳过。新增测试通过真实QThread检查超时/及时结束；独立负例额外验证无Qt parent、仅挂在控件属性的线程仍能持续被检查，timeout=.3返回False且线程仍存活，最后释放线程并wait成功。

## 仍存在的确认问题

### R2-G1 / P2：素材库取消入库仍绕过工作台活动任务门禁

定位：`gui/views/material_library.py:539` 附近 `uncatalog_selected()`（当前取消入库入口及确认后均无busy检查）；`accumulate_selected_to_scope` 的新增门禁没有覆盖此入口。

独立探针建立同一顶级窗口下工作台和素材库、真实临时库、2项工作范围，令工作台真实QThread运行并确认 `_has_active_background_task()` 为True。素材库调用真实 `uncatalog_selected()`、确认Yes后，选中素材记录立即被SQLite删除，worker仍 `isRunning=True`。原始工作范围路径列表并未改变，不能称路径列表被改；但有效库记录已经删除，活动分析/文件任务的目标库状态仍会被跨页改变。

探针的 `assert service.db.get_video_by_path(paths[0]) is not None` 失败。建议将跨页写入门禁集中复用，并在取消入库确认前后都检查；以真实库记录保留、未执行删除及活动worker引用不变作为验收。

### R2-G2 / P2：手动400恢复中央入口缺少IO/跨页门禁及确认后复查

定位：`gui/widgets/recovery_controls.py:1108` 的 `launch_manual_recovery()`；工作台当前新增门禁仅在右键菜单外层，工具栏回调（约1173）和素材库菜单（`material_library.py:471`）仍直接调用中央入口。

中央入口只在开始时检查服务 `_analysis_run_lock`，不能识别扫盘、改名、导出等IO任务，也不在恢复对话框返回后重新检查。独立探针让真实工作台 `_io_worker` QThread于恢复确认期间开始运行，再从真实中央入口继续；该入口仍然构造并启动新的AnalysisWorker。只把远程分析Worker换成记录入参的安全替身，不发API、不制造线程冲突崩溃；直接证据是IO仍存活且 `created` 已有一组恢复目标。

建议中央入口复用当前页面及宿主工作台活动任务门禁，且在对话框返回后再检查busy及服务分析锁，防止工具栏/跨页入口绕过。未授权直接扩大修改，本审查未改源码。

## 验证记录

```powershell
rtk proxy python -X utf8 -m pytest tests/test_review_gui_actions.py tests/test_gui_review_regressions.py tests/test_data_directory_gui.py tests/test_tag_work_views.py -q -rs
# 109 passed in 89.33s，无skip
rtk proxy python -X utf8 -m pytest .scratch/review-cycle-20261114/gui_r2_probe.py -q -s
# 2 failed, 3 passed in 3.21s
```

独立探针的2个失败均为上述确认缺口的业务断言，3个通过分别覆盖范围外选择不扩大、确认保持冻结选择、无parent真实线程持续等待。所有临时线程均在finally中释放并wait成功；探针无遗留活线程。

## 限制与下一步

可见1554×930右侧超屏由主 Agent 与布局负责人继续调查，本报告未把它算作已修；`tools/manual_gui_test.py` 超时后仍销毁/清理的问题已由主 Agent另行派修，本报告未将其列为完成。其他界面缩放/多显示器/Windows10不由本次离屏测试证明。

只新增本报告及 `gui_r2_probe.py`，没有修改产品或tests，没有commit、删除目录、读取真实用户配置或请求公网API。请主 Agent确定G1/G2方案并分配修复，完成后再独立回归。
