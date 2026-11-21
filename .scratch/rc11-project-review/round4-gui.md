# 第四轮：详情按钮文字被挤掉

Status: claimed

执行作者 api；独立复核 desktop。保持既有代理，不新增、不提交。

## 主审复现和方案

主审原生截图 `.release-build/review-rc11-main/screens/0-1.png` 在 Windows11 150%DPI、1280x780逻辑窗口中显示详情底部四按钮“保存修改 / AI 推荐标签 / 生成故事板 / 导出标签云”文字被截断，分别约65逻辑像素。gui/widgets/detail_panel.py:114 使用 QHBoxLayout 在约290宽可用区强挤四按钮。现有 tests/test_review_responsive_layout.py 只断言矩形在面板内，无法检出文字裁切。

已读 detail_panel.py、flow_layout.py 和响应布局测试。明确修法：复用项目现有 FlowLayout(margin=0, spacing=8) 替换该四按钮的单行布局，以 sizeHint 自动换行。去掉 addWidget 的 stretch 参数；不改按钮文字、点击信号、启停规则、功能和固定高度。不要另造布局、不扩大到媒体预览条。

api 仅修改 gui/widgets/detail_panel.py、tests/test_review_responsive_layout.py。扩展现有几何验收，对这四个实际按钮断言宽度>=minimumSizeHint/sizeHint所需宽（根据真实Qt测量），并保持无重叠、在面板内。必须先红再绿，不以改小字体或缩短文字通过。运行响应布局及详情编辑/相关GUI回归，再用原生 render.py 复跑截图（processEvents+sleep已修正，不用长qWait）。结果写入此文件。

desktop 等作者完成后只读运行原生窗口 1280x780 / 1554x930，检查四按钮文本、相互不重叠和可点击，明确布局与真实关闭均成功。不能因为都在面板内就推断文字可读，需视觉和width/sizeHint证据。报告后主审重新冻结并安排最终新EXE。

产品只做此局部布局修改，保持用户原配置和素材，所有数据来自隔离合成环境。失败保留证据，禁止未经决定扩展修复。

## 作者验证

- 仅修改约定两文件。详情四按钮使用现有 `FlowLayout(margin=0, spacing=8)`，保留文字、40 像素高度、事件与启停逻辑；测试检查按钮实际宽度不小于真实 Qt `sizeHint/minimumSizeHint`、互不重叠、位于面板内。
- 修复前：`test_window_pages_fit_with_reachable_settings_save[1280-780]` 检出“保存修改”宽 65、需 80；`[1554-930]` 检出“AI 推荐标签”宽 84、需 124，2 failed / 1 passed。
- 修复后：`rtk proxy python -X utf8 -m pytest -q tests/test_review_responsive_layout.py tests/test_review_gui_actions.py tests/test_gui_review_regressions.py --tb=short --basetemp=.release-build/review-rc11-api/responsive-green` 为 **103 passed**，31 个既有 Qt 弃用警告。
- 原生 `render.py` 成功生成 1280×780 工作台、素材库、设置页截图，`closed True True`。作者已查看 `screens/0-1.png` 与 `screens/1-1.png`，四按钮以两行呈现，文字完整。独立复核者 desktop 已接收当前差异，将再验证两种窗口尺寸、可点击和真实关闭。
