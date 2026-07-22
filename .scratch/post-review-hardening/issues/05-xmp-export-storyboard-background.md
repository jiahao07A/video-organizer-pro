# 05 — XMP / 导出 / 故事板后台化

**What to build:** 工作台「同步元数据 (XMP)」、导出 FCPX、导出 ALE，以及详情「生成故事板」等**磁盘/CPU 重任务**在后台执行，主线程不假死。任务进行中有进度或忙碌反馈；完成/失败有明确提示。本票不改变导出字段语义与操作目标集规则（依赖 02）。

**Blocked by:** 04 — 工作范围扫盘后台化

**Status:** resolved

**Parent:** [spec.md](../spec.md)

## Acceptance criteria

- [x] 大批量 XMP 同步时界面可响应或有明确忙碌进度，结束后条数提示正确
- [x] FCPX / ALE 导出在后台完成，成功/失败可感知
- [x] 故事板生成不阻塞主线程到假死；失败可提示
- [x] 与分析 Worker 并存时无互相踩进度回调的明显故障（至少串行互斥或独立回调）

## Comments

### 2026-07-22 implement

- `gui/workers/io_worker.py`；工作台导出/XMP、详情故事板走 `run_io_job`
- 与标签 AI / 分析任务通过 `_io_worker` 互斥提示