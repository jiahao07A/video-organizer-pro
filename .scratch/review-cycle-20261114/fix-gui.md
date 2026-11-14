# GUI 修复执行与回归

Status: 已按执行包完成，等待主 Agent 独立复审
对应：R1-C 的 5 项已确认问题。

## 修改文件与行为

- `gui/views/workstation.py`：右键选中分析和主分析按钮共用 `_confirm_and_start_analysis(paths, snapshot)`；右键仍仅使用选中项，限定于工作范围，没有选中不回退全部。强制覆盖及自动 XMP 写入采用同样确认文案，点“否”不构造 worker。分析启动前和确认后检查活动分析/IO/恢复任务，不覆盖当前 worker。
- 同文件新增 `_reject_busy_operation()`：分析、移出工作范围、批量替换、重命名、回滚、导出、同步 XMP 和物理整理入口统一拒绝忙碌调用；有确认框/路径框的入口在返回后再次检查。右键恢复分支先检查该门禁。
- `gui/views/material_library.py`：累加前检查顶层主窗工作台的活动分析/IO/恢复线程；空闲时 `append_work_scope(scan=False)` 轻量改变范围，再复用工作台 `background_scan_work_scope()` 后台入库，避免同步 GUI 扫盘。
- `gui/widgets/detail_panel.py`：取消“合并标签”时写入空 `tags=[]`，单选和批量一致；合并模式空输入保持已有标签。
- `core/manual_test_environment.py`：线程去重集合放入轮询内，同一轮避免父子树/属性重复等待，后续轮次重新检查仍在运行的 QThread。
- 仅新增 `tests/test_review_gui_actions.py`，未修改原测试文件与禁止的服务文件。

## 验证结果

```powershell
rtk proxy python -X utf8 -m pytest tests/test_review_gui_actions.py -q -rs
```

**51 passed in 38.28s**。覆盖单/批量真实 SQLite 标签清空与合并保留、两种分析入口的强制/XMP确认拒绝、右键空选择及精确目标、三种活动 worker 下十种副作用入口门禁、确认中出现活动任务、跨页累加门禁、后台扫描实际 QThread、长线程超时/正常结束，以及 MainWindow 在真实 QThread 运行时拒绝关闭且不销毁该线程。

```powershell
rtk proxy python -X utf8 -m pytest tests/test_gui_review_regressions.py tests/test_analyze_operation_pipeline.py tests/test_manual_test_environment.py tests/test_post_review_hardening.py tests/test_ops_filter_scope.py -q -rs
```

**76 passed in 69.99s**，无跳过。

`git diff --check`、修改文件 `compileall` 和新增测试 `ruff --select F401,F811,F821` 全部通过。

## 仍需主 Agent 裁决的范围外退出风险

`tools/manual_gui_test.py` 的 finally 在 `wait_for_background_work()` 返回 False 后仍继续 `window.close()/deleteLater()` 及 `environment.close()`。本次等待函数修复使超时能正确报告，但脚本本身仍可能销毁活动线程或清理线程正使用的临时数据。该文件未在执行包允许修改范围内，因此仅报告，未扩大修改。正式 `MainWindow.closeEvent` 有活动任务门禁，本轮新增真实 QThread 测试已确认其拒绝关闭。

本轮 GUI 测试为 Windows 11/Qt offscreen；不替代 Windows 10、可见桌面 DPI、真实媒体设备、公网 AI 和用户人工验收。未提交、未删除目录、未读写用户素材与配置。
