# 05 — 标准词 AI 助手（别名 + 改组）

**What to build:** 在标签库对某个**标准词**请求 AI 助手（任务槽：标准词 AI 助手）：建议可挂别名、是否改到另一标签组。用户可分项确认后：挂别名、或执行**标签移动**。本票**不提供**「并入另一标准词」（合并走近义巡检）。未确认零写库；可注入假 AI。

**Blocked by:** 01 — 任务模型路由

**Status:** resolved

**Parent:** [spec.md](../spec.md) · ADR-0004 · ADR-0006

## Acceptance criteria

- [x] 标签库标准词有可用入口（如右键/行内）打开 AI 助手
- [x] 建议含别名列表与可选推荐组（改组）；无 merge-into-other 动作
- [x] 可只采纳别名、只采纳改组、或都采纳；未勾选部分不写
- [x] 确认别名 → 写入别名表；确认改组 → 标签移动，标准词正文不变
- [x] 未确认零写库（假 AI 可测）
- [x] 模型失败时有可理解提示或安全降级（不静默写库）

## Comments

### 2026-07-22 to-tickets

- 用户批准发布；主接缝 B 之一

### 2026-07-22 implement

- Status: resolved
- `suggest_standard_tag_assist` / `apply_standard_tag_assist` + 右键「标准词 AI 助手」