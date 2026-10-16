# 系统设置页强制增高主窗口

Status: resolved
Category: bug

## 背景与问题

从主窗口切换到「系统设置」时，AI 引擎页的内容高度被 Qt 当作系统设置页的最小高度。主窗口因此从默认高度强制增高到约 1160 像素，在常见屏幕上把系统设置底部按钮移出可视区域。

## 目标

系统设置的内容在主窗口当前可用高度内通过页内滚动显示；切换页面不改变主窗口高度，也不让「恢复默认」和「保存所有设置」按钮离开系统设置页底部。

## 范围内

- 将系统设置「AI 引擎」页的长内容放入可调整大小的滚动区域。
- 增加 Qt 离屏回归测试，覆盖从工作台切换到系统设置时主窗口不被强制增高以及底部按钮仍在页面几何范围内。

## 范围外

- 不调整设置项、配置键、保存逻辑或 AI 供应商行为。
- 不重做系统设置页面视觉样式，也不处理其他页面的窗口尺寸问题。

## 验收标准

- 切换到系统设置后，主窗口高度不超过切换前高度。
- 系统设置底部保存按钮仍位于设置页可见布局范围内。
- 既有 GUI 和设置测试保持通过。

## 方案与回滚

AI 引擎页保留现有控件和信号，仅增加 `QScrollArea` 包裹内容；失败时可回退本次 `gui/views/settings.py` 与回归测试的单一修复提交，不涉及用户素材或管理数据。

## 验证记录

- 回归测试先失败：`test_switching_to_settings_does_not_force_window_beyond_current_height`，实际高度从 900 增至 1160。
- 修复后：`python -m pytest tests/test_gui_review_regressions.py tests/test_settings_catalog.py -q`，35 passed。
- 语法检查：`python -m compileall -q gui tests/test_gui_review_regressions.py`，通过。
- 差异检查：`git diff --check`，通过。

## Comments

### 修复记录

AI 引擎页原本直接挂在页签布局中，路由与供应商控件的累计高度参与了主窗口最小高度计算。现在通过可调整大小的滚动区域承载该页内容，使主窗口保持当前尺寸并由页内滚动处理超出部分。
