# 第五轮：小窗口预览控制被详情表单遮挡

Status: claimed

作者 api，独立 desktop；platform 暂停新的安装器/验收等待源码最终冻结，已运行的构建保留，不删除。

## 主审直接证据

主审检查第四轮最新 screens/0-1.png：四按钮已完整，但1280x780/150%DPI中 media_preview 的进度/播放/音量行位于其分配矩形之外，被紧接的表单覆盖。旧截图也已部分裁切，新两行操作按钮使问题更明显。代码为 DetailPanel 中 media_preview 显式 minHeight260；内部 placeholder 和 QVideoWidget minHeight200，控制单行内容宽度也超出320面板。仅验证控件在整面板内或直接发送QTest事件不能证明实际鼠标可达。

已读 gui/widgets/detail_panel.py 前155行、完整media_preview.py布局及既有FlowLayout和响应测试。不要再用硬编码压缩高度修补。

## 已定实现

1. DetailPanel 保留顶层批量提示、合并选项及底部四个FlowLayout操作按钮。将预览标题、MediaPreviewWidget、详情表单三个部分放入**一个** Qt QScrollArea 内容容器（QVBoxLayout），占剩余空间。复用已有表单容器，移除原来只包表单的嵌套滚动区。使整个内容按真实最小尺寸布置，小窗口纵向滚动可达；表单行/信号/字段设置保持。
2. 删除 DetailPanel 对媒体部件显式260高度覆盖，让布局使用真实内容需求。保持画面200最小高度和媒体功能。
3. MediaPreviewWidget 的控制行改用现有FlowLayout：播放、音量label+slider一组、倍速label+combo一组、定位按钮。用QWidget+QHBoxLayout将各组绑定，0内边距，保留90宽音量及现有属性/信号/文字；FlowLayout确保内容不压窄文字。进度行保持现行逻辑。
4. tests/test_review_responsive_layout.py 对滚动内容以确保控件滚入 viewport 后实际几何命中来验收，不要求隐藏在滚动区下方的所有按钮同时在面板内；固定四操作按钮依旧检查文字、相互无重叠和窗口内。新增实际媒体控制边界：播放器自己的可见子控件都在其矩形内且不相互遮挡；滚入viewport后 childAt/真实点击能命中播放和倍速，不用直接emit。测试选择真实合成视频并确认位置推进，避免空预览几何假绿。可以扩展同测试模块，别删旧测试。
5. 仅限 detail_panel.py、media_preview.py 和响应布局测试，禁止全局主题、业务/AI/文件操作变化，不提交。

先新增几何/遮挡判定取得红，再修。运行原103项组合及媒体预览相关测试。desktop 在原生1280x780和1554x930、150%DPI下分别测试工作台和素材库，真实滚动、播放/暂停、倍速、详情字段到达、保存按钮可用、正常关闭，检查截图。产品外的文件均隔离合成。

主审会审查差异及原生证据，全部完成后才通知platform最终构建。不做其他设计扩展，不新增代理。

## 作者验证

- 仅改约定三个文件。一个 `content_scroll` 容纳预览标题、播放器、原详情表单；取消 260 像素的覆盖高度，滚动内容按布局最小尺寸计算。播放器的播放、音量组、倍速组、定位按钮复用 FlowLayout；画面最小 200、音量 90 宽、业务事件及固定操作区保持。
- 在修复前新增工作台/素材库 × 1280×780/1554×930 四组真实 MP4 用例，全部失败：窄工作台播放按钮仅 51 像素而需 54，其他场景“在文件夹中显示”只有 74/106 而需 124。
- 修复后响应模块 7 项通过。新用例按播放器矩形检查六个控制元素的边界及相互不重叠；滚入可视区后通过主窗口 `childAt` 确认目标，再点击实际命中控件。真实媒体播放位置超过 200ms、暂停、倍速改为 1.25、摘要滚入可视区及保存按钮命中均通过。文本编辑框需按完整矩形滚入，不能只使用默认“光标可见”作为完整可达证据。
- `rtk proxy python -X utf8 -m pytest -q tests/test_review_responsive_layout.py tests/test_review_gui_actions.py tests/test_gui_review_regressions.py tests/test_data_directory_gui.py tests/test_rename_preview_lock.py tests/test_runtime_performance.py --tb=short --basetemp=.release-build/review-rc11-api/preview-complete` 为 **150 passed**，56 条既有 Qt 弃用警告，耗时 85.58 秒。`git diff --check` 通过。
- 当前作者检查未见新的阻断。desktop 已收到产品落盘通知，继续用 Windows 实际鼠标和原生截图做独立复核；本轮未提交。

## 独立复核退回：表单把滚动内容撑宽

desktop 实际鼠标验证1280工作台通过；1280素材库仍横向滚动，viewport280，倍速右界285，时间文本也裁切。主审已看 failure.png 确认。根因 QFormLayout 默认不换行，字段最小宽+标签撑宽整个统一内容，媒体也按超出viewport的宽度排布。

补充决定：api 在原form_layout设置 `setRowWrapPolicy(QFormLayout.WrapLongRows)`，让窄面板的字段标签与编辑器自动分行；不缩小字体、不禁用滚动条掩盖问题。新增四场景真实媒体/实际行内容下 content_scroll.horizontalScrollBar().maximum()==0 的断言，等待布局稳定后判断；若此最小修正仍不足，先报告实际撑宽子控件，不盲目设固定宽度。随后复跑响应模块及相关套件。desktop 按原执行包重验四场景、确认无需水平滚动，保留真播放/滚动/保存证据。

## 表单换行补充修复与作者回归

- 产品补充修正仅在原有表单布局增加 `form_layout.setRowWrapPolicy(QFormLayout.WrapLongRows)`，窄窗口允许标签和字段自动分行，不设置固定宽度，不隐藏滚动条。
- 响应用例写入并选择具有真实分类、标签、摘要和转录内容的临时数据库行，等待实际列表及详情加载，并验证四场景下 `content_scroll.horizontalScrollBar().maximum() == 0`。修正前 1280×780 的两个页面失败、1554×930 两个页面通过（2 failed / 2 passed）；修正后响应模块 7 passed。
- 相关六模块套件（responsive_layout、gui_actions、gui_review_regressions、data_directory_gui、rename_preview_lock、runtime_performance）在 `.release-build/review-rc11-api/wrap-complete` 隔离目录运行：**150 passed，56 条既有 Qt 弃用警告，耗时 86.43 秒**。
- `git diff --check` 通过。已通知 desktop 使用 `.release-build/review-rc11-desktop/round5-preview-final` 保留独立的 Windows 实际鼠标、真实播放和滚动证据；等待主审统一审阅后冻结构建，本代理不提交。
- desktop 独立复核已通过：Windows 原生 150%DPI、工作台/素材库 × 1280×780/1554×930 四场景，实际操作系统鼠标滚轮及点击、原生键盘选择 2 倍速；横向滚动最大值全部为 0，真实播放/暂停/恢复、分类/标签/摘要/转录滚动可达、保存落盘和正常退出均通过。探针窗口视频区域截图均检测到约 9 万彩色像素，确认实际渲染彩条，非仅播放进度假绿。结果文件 `.release-build/review-rc11-desktop/round5-preview-final/result.json` 已由作者复读核实；本轮无新增阻断，待主审最终接收。
