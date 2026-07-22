# 01 — 分析任务策略纯逻辑（重试 + 进度归约）

**What to build:** 提供可单测的分析任务策略：瞬时错误是否可重试（T1）、调用层/单条层/批次补跑次数与条件、以及由进度事件归约出的总/子进度快照与行临时态 map。

**Blocked by:** None — can start immediately

**Status:** resolved

**Parent:** [spec.md](../spec.md)

## Acceptance criteria

- [x] `is_retriable` T1
- [x] 调用层次数边界
- [x] 单条层次数边界
- [x] `should_batch_rerun` 矩阵
- [x] 进度归约首轮/补跑
- [x] 行临时态 map
- [x] 退避可注入
- [x] `tests/test_analysis_job_policy.py` 11 passed

## Comments

### 2026-07-22 implement

- 新增 `core/analysis_job_policy.py`
- 测试 11 passed