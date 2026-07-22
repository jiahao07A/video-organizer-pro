# 03 — 近义巡检真 AI

**What to build:** 近义巡检**默认走大模型**（任务槽：近义巡检）生成合并建议清单；失败时规则降级。合并仍须人工确认后执行（别名挂接 + 视频标签 bulk 归一到保留词）。入口保持次要（顶栏「更多」等）。可注入假 AI 单测。

**Blocked by:** 01 — 任务模型路由

**Status:** resolved

**Parent:** [spec.md](../spec.md) · ADR-0004 · ADR-0006

## Acceptance criteria

- [x] 打开近义巡检时默认调模型生成建议，不再仅默认规则
- [x] 模型失败时降级规则巡检，仍须确认才合并
- [x] 确认后：被合并词成为保留词别名，视频上旧标准词归一到保留词，库内去掉冗余标准词（与既有 apply 语义一致）
- [x] 未确认零写库（假 AI 可测）
- [x] 入口仍为次要运营入口，不抢待审主路径

## Comments

### 2026-07-22 to-tickets

- 用户批准发布；主接缝 B 之一

### 2026-07-22 implement

- Status: resolved
- `audit_synonyms(use_model=True)` + parse/规则降级；既有确认合并 UI