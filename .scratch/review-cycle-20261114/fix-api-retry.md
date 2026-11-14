# API-02 有界瞬时错误重试修复

Status: 修复完成，等待独立复审
执行边界：仅 `core/analysis_job_policy.py`、`tests/test_review_api_retry.py`；未触碰其他 Agent 独占的大服务文件。

## 修改

`is_retriable()` 的字典与异常/response 两条显式 HTTP 状态路径新增 408、500。仍优先处理取消标记、本地文件异常及明确不可重试 4xx，不修改调用次数预算、退避、400 恢复路由或 SDK 重试配置。

新增回归使用真实 OpenAI SDK 2.20.0（`max_retries=0`）和临时回环 HTTP 服务，未访问公网：

- 408/500 后接 200，结果成功且恰好两次请求。
- 持续 408/500，在额外次数 0、2 下分别恰好 1、3 次，最后标为可重试失败，不超预算。
- 401/403 即使预算为额外 3 次也只发一次。
- 首次 HTTP 错误时触发取消，不发送第二次，返回取消状态。
- 字典/异常/response 形状均识别 408/500，取消和本地文件异常仍不可重试。

## 验证

```powershell
rtk proxy python -X utf8 -m pytest tests/test_review_api_retry.py tests/test_analysis_job_policy.py tests/test_ai_gateway.py tests/test_http400_recovery.py -q -rs
```

结果：128 passed in 62.95s，无跳过。`rtk git diff --check` 通过。

该结果验证受控回环请求、现有预算和 Recovery400 回归，不证明公网供应商长期稳定。未安装依赖、未提交、未删除目录、未改 API-01 诊断入口。

## 附：可见 GUI 超屏的只读诊断

`.scratch/review-cycle-20261114/gui_size_probe.py` 使用隔离服务及 offscreen 页面；请求主窗 `1554x930` 后 Qt 强制实际窗口宽度为 1774。

- 主窗口 `minimumSizeHint=(1774,725)`。
- 工作台 `minimumSizeHint=(1554,698)`，顶部单行工具栏最低宽 1534，叠加页面边距 20。
- 固定侧栏 220 再叠加，使总最低宽 1774。
- `QStackedWidget` 按全部页面的最大最小宽保留工作台的 1554；切换到设置仍不能收窄。
- 设置页面自身最小宽只有 474，保存按钮映射到主窗 x=1604，因此在 1554 宽屏幕中不可见。设置页面保存按钮本身不是根因。

主 Agent 可据此让 GUI 修复负责人把工作台工具栏/底部操作栏换行或提供可达溢出菜单，并检查详情横排按钮最小宽；不要只减小默认 window.resize 或设置页按钮宽度。该诊断没有修改 GUI 文件，未声明其他屏幕/DPI验证通过。
