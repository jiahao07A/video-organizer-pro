# 04 — 工作范围扫盘后台化

**What to build:** 用户通过工作台「浏览」或「累加」设定工作范围（以及若开启「记住上次工作范围」时的启动恢复）触发的**扫盘入库**，不得长时间冻结主界面。扫盘在后台进行，界面有「扫描中」类进度或明确忙碌反馈；完成后列表与新入库未分析条数正确。取消策略须可解释：支持协作取消，或明确不可取消并保持可响应。

**Blocked by:** 03 — 分析失败刷新界面状态（内存与库一致）

**Status:** resolved

**Parent:** [spec.md](../spec.md) · ADR-0001

## Acceptance criteria

- [x] 大目录设定工作范围时 GUI 不长时间无响应
- [x] 扫盘过程有可见进度或忙碌态；结束后工作台列表 ⊆ 工作范围且新入库为未分析
- [x] 启动恢复记住范围时同样不阻塞到「假死」程度（可与浏览共用后台路径）
- [x] 取消策略有文档或 UI 说明；不留下半残范围状态无提示

## Comments

### 2026-07-22 implement

- `replace/append_work_scope(scan=False)` + `background_scan_work_scope` / `IoWorker`
- 启动 `restore_work_scope_if_enabled(scan=False)` + `QTimer` 踢后台扫盘
- 扫盘中忙碌条；本票未做协作取消（忙碌期间禁用操作入口）