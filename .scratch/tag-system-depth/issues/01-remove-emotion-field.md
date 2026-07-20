# 01 — 分析结果去情绪并贯通筛选/导出/详情

**What to build:** 分析结果不再包含独立「情绪」字段：分析提示与写入只保留分类、摘要、标签（含标签组）；高级筛选去掉情绪维；详情、导出、重命名不再使用 emotion / Emotion；情感与视听基调改由氛围标签表达。用户侧表现为入口统一、AI 少填一套近义字段。

**Blocked by:** None — can start immediately

**Status:** resolved

**Parent:** [spec.md](../spec.md) · ADR-0003 · 交叉 `workbench-library-split` 情绪筛拆除

## Acceptance criteria

- [x] 分析提示词 / 期望 JSON 不含 emotion 字段说明与键
- [x] 分析结果写入与读取路径不再依赖 emotion；旧数据硬删或忽略，无只读兼容入口
- [x] 详情面板不再展示独立情绪
- [x] 高级筛选无独立情绪维；可用标签（含氛围标准词）按基调收窄
- [x] 重命名变量与导出（含 ALE Emotion 列等）不再使用 emotion
- [x] 服务层/筛选测试：无 emotion 依赖；标签筛选既有 any/all 行为不回归

## Comments

### 2026-07-20 to-tickets

- 用户批准 5 票发布；可与 02 并行开工

### 2026-07-20 agent

- Status: claimed → resolved
- 实现：提示词 JSON 去掉 emotion；upsert/读出硬清 emotion（列保留仅兼容旧库）；详情去「情感氛围」行；筛选去情绪维；重命名 `{emotion}`→空；ALE 去掉 Emotion 列；默认 ale_columns / 设置提示同步
- 测试：`tests/test_slim_analysis_fields.py`、`tests/test_ops_filter_scope.py`、`tests/test_work_scope.py` 对齐 ADR-0003