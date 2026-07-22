# 01 — 任务模型路由（六槽 + 回退 + 设置 UI）

**What to build:** 设置中可为六个 AI 功能各自指定**模型供应商 + 模型名**（视频分类、标签生成、内容描述、待审词 AI、近义巡检、标准词 AI 助手）。**当前供应商**仅作未配置功能的默认回退。旧「档案内三模型 + 仅当前供应商」配置可迁移；保存后新请求按路由解析；进行中分析本轮不中断。解析逻辑可单测（ADR-0006）。

**Blocked by:** None — can start immediately

**Status:** resolved

**Parent:** [spec.md](../spec.md) · ADR-0006

## Acceptance criteria

- [x] 六槽可各自选择供应商档案与模型名；设置 UI 可编辑并保存落盘
- [x] 某槽未单独配置时回退到当前供应商（及合理默认模型名）；可单测
- [x] 从旧档案内三模型迁移后，分析三槽仍可用；标签库三槽默认可回退
- [x] 所有新 AI 请求经统一路由解析取 Key/URL/model；禁止旁路死读与路由脱节的旧扁平真相
- [x] 供应商上限五个不变；删供应商后相关槽回退行为可解释
- [x] 进行中分析不因改路由/当前供应商而中断本轮

## Comments

### 2026-07-22 to-tickets

- 用户批准 8 票发布；主接缝 A；frontier（可与 07 并行）

### 2026-07-22 implement

- Status: resolved
- `resolve_task_route` / `client_for_task` / 设置页六槽 UI / 迁移与删供应商回退；`tests/test_model_providers.py` 覆盖
