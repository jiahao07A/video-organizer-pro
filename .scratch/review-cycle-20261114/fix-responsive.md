# GUI 响应式布局修复

Status: 修复及定向验证完成，等待独立复审

## 问题与修复

旧工作台单行工具栏最低宽 1534，加页边距和固定侧栏后主窗口最低宽 1774。屏幕宽 1554 时窗口超屏；页面栈保留工作台最小宽，导致切换设置后保存按钮仍不可见。

按主 Agent 方案复用仓库 `FlowLayout`，不增加框架或改变按钮操作语义：

- 工作台顶部工具栏和底部操作按钮改为可换行布局，移除仅装饰的 spacing/stretch，全部原控件、顺序和信号保持。
- 素材库顶部工具栏同样换行；仅修改 `setup_ui`，没有覆盖其他代理的取消入库门禁修复。
- 两页目标摘要启用换行，不再以整行摘要撑大窗口。
- `FlowLayout` 正确处理边距、忽略隐藏项、把继承的负间距限制到非负值，并在可用宽度内布局控件；标签气泡沿用原实现，不复制另一个流布局。

没有通过增加无限横向滚动隐藏问题，没有改变主窗口页面流程、业务操作或任务门禁。

## 几何证据

新增 `tests/test_review_responsive_layout.py` 先在旧实现失败：请求宽 1280，实际为 1774。
修改后离屏验证两个窗口尺寸 `1280x800`、`1554x930`：

- 实际窗口不被最小宽高强制扩大；逐页创建工作台、素材库、标签库、设置、监控五页仍满足窗口边界。
- 1280 宽下工作台/素材库顶部实际分为多行，流布局中每个可见控件的矩形均在布局内且互不重叠。
- 详情面板可见按钮矩形全部在面板内，设置保存按钮完整位于主窗口内。
- 独立布局场景验证隐藏按钮不占行，边距和换行高度准确。

`gui_size_probe.py` 修复后：1554x930 请求保持实际 1554 宽；初始窗口 `minimumSizeHint=(608,725)`，创建设置后 `(694,725)`；保存按钮主窗 x=1384、宽150，完整处于 1554 内。原为 x=1604、宽150。

## 验证记录

```powershell
rtk proxy python -X utf8 -m pytest tests/test_review_responsive_layout.py tests/test_layout_search_fixes.py tests/test_gui_review_regressions.py tests/test_review_gui_actions.py tests/test_review_gui_uncatalog_gate.py tests/test_tag_work_views.py tests/test_material_display_fixes.py -q -rs
```

首轮组合138 passed in 128.81s，30条已有Qt弃用告警，以及1条销毁标签页后其延迟统计任务触及已删除对象的线程告警。

进一步确认标签页使用无保存引用的 `QTimer.singleShot(500, ...)` 延迟启动Python统计线程，单纯等活动线程无法覆盖尚未启动的任务。该产品生命周期问题已发回主Agent分派；本布局执行包不包含标签页产品逻辑，因此没有擅自修改。

几何测试 teardown 已明确等待标签页已排队的首次500ms统计刷新，再停止自身子定时器、处理事件、等待Qt/Python活动检查归零后正常关闭。它不承担快速退出验收，没有屏蔽告警。修改清理后的精确复跑：

```powershell
rtk proxy python -X utf8 -m pytest tests/test_review_responsive_layout.py tests/test_layout_search_fixes.py -q -rs
```

结果16 passed in 17.02s，仅30条原Qt弃用告警，没有线程异常告警。产品布局代码在138项组合之后未再修改；最终全量回归由主Agent统一执行。

`ruff check tests/test_review_responsive_layout.py gui/widgets/flow_layout.py --select F401,F811,F821` 与 `git diff --check` 通过。

## 边界与下一步

本代理仅验证96 DPI离屏几何，没有宣称160%桌面缩放、多显示器或Windows 10通过。可见GUI复核由主Agent进行；独立代理应检查FlowLayout标签用途回归、工具栏换行及设置保存可达。产品文件编辑权已释放。未提交、未删除目录、未修改服务或其他业务逻辑。
