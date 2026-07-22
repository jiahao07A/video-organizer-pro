# 06 — 待审词 AI 建议

**What to build:** 在待审词运营入口，对每条待审词提供 **待审词 AI 建议**（批准为新标准词及推荐标签组 / 挂为某标准词别名 / 建议丢弃，可附简短理由）。用户确认后才写入；未确认零写库。禁止模糊自动贴词与静默入库（ADR-0004）。AI 客户端可注入，便于单测。

**Blocked by:** None — can start immediately（可与 01–03、07、08 并行；依赖既有 tag-system-depth 待审 API）

**Status:** resolved

**Parent:** [spec.md](../spec.md) · ADR-0004

## Acceptance criteria

- [ ] 对待审词可请求 AI 建议；输出动作枚举含 approve_standard / link_alias / discard（及必要参数）
- [ ] 未确认时不修改标准词库、别名表、待审状态（可测，假 AI）
- [ ] 用户确认后走既有 resolve 路径写库；成功后队列状态正确
- [ ] 禁止用 AI 结果绕过标签归一硬过滤（不静默把库外词当标准词落视频）
- [ ] 标签库 UI 有可用入口展示建议并一键采纳（仍须确认）

## Comments

### 2026-07-21 to-tickets

- 用户批准发布；主接缝 C 之一；frontier

### 2026-07-21 implement

- Status: claimed → resolved
- 主会话批量实现 analysis-library-ux-tag-ai
