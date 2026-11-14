# R2-G1：跨页取消入库门禁

Status: 修复与组合回归完成，等待独立复审

仅修改 `gui/views/material_library.py`：抽取 `_reject_workstation_busy(operation)`，在 `uncatalog_selected()` 的确认前与用户确认 Yes 后分别调用；累加入范围入口复用同一门禁。忙碌时不删除素材记录、不改工作范围、不替换线程。空闲取消入库维持现有语义：删除选中的库记录、保留磁盘文件及显式范围路径。

新增 `tests/test_review_gui_uncatalog_gate.py`：真实 QThread、真实临时 SQLite，覆盖分析/IO/恢复三种 worker 在确认前已经运行和确认期间才启动的六种场景，核对记录保留、范围不变、线程仍运行及引用未被覆盖；另覆盖空闲 Yes/No 与原文件保留。

```powershell
rtk proxy python -X utf8 -m pytest tests/test_review_gui_uncatalog_gate.py -q -rs
# 8 passed in 4.26s
```

`git diff --check` 和新测试 `ruff --select F401,F811,F821` 通过。初版测试把空闲确认 Yes 的显式范围预期误写成移除路径，核对现有取消入库实现及 R2 报告后修正测试，未修改产品原有范围语义。

组合回归：`tests/test_review_gui_uncatalog_gate.py tests/test_review_gui_actions.py tests/test_gui_review_regressions.py tests/test_ops_filter_scope.py`，**109 passed in 44.13s**。

文件编辑已释放并通知布局负责人；未修改 `setup_ui`、服务层、其他原测试，未提交、未删除目录。
