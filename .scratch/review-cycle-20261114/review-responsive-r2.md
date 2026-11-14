# R2 响应式布局独立只读复审

Status: 当前布局修复通过；未发现可确认布局回归
审查者未参与本轮布局实现。仅审查布局相关差异，不将本人此前编写的业务门禁计入本报告。

## 审查范围与结论

- `gui/views/workstation.py::setup_ui`：顶部与底部操作栏改用现有 FlowLayout，装饰性 spacing/stretch 移除；控件、顺序与连接保持。操作目标摘要启用换行。
- `gui/views/material_library.py::setup_ui`：顶部工具栏同样换行，目标摘要启用换行。
- `gui/widgets/flow_layout.py`：边距、非负 spacing、隐藏项不占位、宽度约束和 heightForWidth 计算。核对实际标签气泡容器的动态重建用途。
- `tests/test_review_responsive_layout.py`：真实 MainWindow、两尺寸、五页面与控件几何；测试没有替身布局或只检查固定常数，能检测原窗口被强制扩大的问题。

当前离屏验证在 **1280×800、1554×930** 逐页显示工作台、素材库、标签库、设置、监控时，窗口未被最小尺寸撑大；工具栏矩形位于布局内且无重叠，1280宽出现多行；详情面板可见按钮完整位于面板，设置保存按钮完整位于主窗口。

重新运行 `gui_size_probe.py` 得到实际 1554×930，工作台初始主窗最小提示608×725，创建设置后694×725；保存按钮映射主窗 x=1384、宽150，右边界1534，位于1554内。历史1774宽的结果仅作为修复前线索，当前尺寸由本轮重新执行取得。

## 共享 FlowLayout 独立探针

新增 `.scratch/review-cycle-20261114/responsive_r2_probe.py`，仅创建离屏控件，无产品修改：

1. 显示三按钮→隐藏中间按钮→重新显示→动态添加→移除，逐步验证换行高度、显示状态、计数、边界与互不重叠。
2. 非对称边距与非零矩形偏移：验证精确起点和两行heightForWidth（162宽对应71高），没有重复计算偏移。
3. 真实 TagFlowWidget 执行多标签→单标签→清空→重新填充；删除延迟对象并处理事件后，实际条数、标签及可见几何正确。

```powershell
rtk proxy python -X utf8 -m pytest .scratch/review-cycle-20261114/responsive_r2_probe.py -q -rs
# 3 passed in 0.86s
```

## 回归及测试清理复核

首次组合25项通过，但有1条标签统计Python线程在TagsView已删除后执行invokeMethod的线程异常告警。布局作者随后修正新几何测试的teardown（停止定时器并处理事件，等待真实后台任务），没有屏蔽警告。

针对**更新后的当前测试**再次运行，将未处理线程异常升级为失败：

```powershell
rtk proxy python -X utf8 -m pytest tests/test_review_responsive_layout.py tests/test_tag_work_views.py tests/test_layout_search_fixes.py -q -rs -W error::pytest.PytestUnhandledThreadExceptionWarning
# 25 passed, 30 warnings in 21.46s
```

最终仅30条已有 `QSortFilterProxyModel.invalidateFilter()` Qt弃用警告；没有未处理线程异常。上述一次通过不能证明所有未来时序不存在生命周期问题，本次没有因该告警擅自修改产品线程实现。

## 边界

当前证据为 Windows 11、96 DPI Qt offscreen，未声称覆盖Windows 10、160%缩放、多显示器、可见桌面操作或全部设置文本长度组合。主 Agent 仍需进行可见窗口复核。本代理只新增报告和只读探针，未修改产品/既有测试、未提交。
