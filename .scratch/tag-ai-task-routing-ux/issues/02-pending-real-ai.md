# 02 — 待审词真 AI（单条）

**What to build:** 待审面板「AI 建议」**默认走大模型**（任务槽：待审词 AI），输出批准标准词 / 挂别名 / 丢弃及参数与理由；失败或无 Key 时规则降级并标明来源。用户确认后才写库；未确认零写库（ADR-0004）。可注入假 AI 单测。冷启动本票不涉及。

**Blocked by:** 01 — 任务模型路由

**Status:** resolved

**Parent:** [spec.md](../spec.md) · ADR-0004 · ADR-0006

## Acceptance criteria

- [x] 单条待审请求 AI 时默认调模型（经待审词 AI 路由），不再仅默认规则
- [x] 模型失败/不可用时降级规则建议，并让用户可知来源（model|rule）
- [x] 输出动作含 approve_standard / link_alias / discard 及必要参数与理由
- [x] 未确认不修改标准词库、别名表、待审状态（假 AI 可测）
- [x] 确认后走既有 resolve 路径写库；成功后队列状态正确
- [x] 不绕过标签归一硬过滤、不静默把库外词当标准词落视频

## Comments

### 2026-07-22 to-tickets

- 用户批准发布；核心路径 01→02→04；主接缝 B 之一

### 2026-07-22 implement

- Status: resolved
- `suggest_pending_tag(use_model=True)` + `_call_tag_ai_json` + UI 标明来源；确认边界单测