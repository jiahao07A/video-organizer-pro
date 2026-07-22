# 03 — 分析失败刷新界面状态（内存与库一致）

**What to build:** 某条视频在分析任务中**最终失败**后，内存中的素材列表与数据库一致，界面刷新后显示失败态（及可得的失败原因），不再继续展示旧的已分析成功结果。成功落库路径保持现有刷新行为。

**Blocked by:** 02 — 导出路径键统一（ALE 对齐 FCPX）

**Status:** resolved

**Parent:** [spec.md](../spec.md)

## Acceptance criteria

- [x] 单条最终失败后，通过服务层列举/工作台刷新可见 status 为失败（或产品等价态），而非旧 analyzed
- [x] 有自动化测试：失败持久化后内存路径与库一致
- [x] 不把 analyzing/retrying 等行临时态写入持久 status（遵守 CONTEXT）
- [x] 不影响成功路径与强制重分析覆盖语义

## Comments

### 2026-07-22 implement

- `_persist_analysis_failure` 清空 L1 `_memory_cache` 强制从 DB 重载
- 测试：`test_analysis_failure_clears_memory_cache`