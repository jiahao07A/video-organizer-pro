# 04 — 批量待审 AI 分拣

**What to build:** 对待审队列一次生成建议清单（与单条同一动作枚举与待审词 AI 路由）；用户**多选**后点「应用选中」才写库；未选中行不写。默认不预选（更安全）。禁止一键静默全写。依赖单条真 AI 路径（02）。

**Blocked by:** 02 — 待审词真 AI（单条）

**Status:** resolved

**Parent:** [spec.md](../spec.md) · ADR-0004

## Acceptance criteria

- [x] 标签库待审侧有批量 AI 分拣入口，可对队列生成建议清单
- [x] 清单展示每条建议动作/参数/理由；支持多选
- [x] 「应用选中」仅处理选中行；未选中即使有建议也不写库
- [x] 应用走与单条相同的 resolve 写库路径；批准须指定组等规则不绕过
- [x] 默认不预选高危全写；可选手动全选但是用户手势
- [x] 未确认/取消清单不写库；假 AI 可测批量边界

## Comments

### 2026-07-22 to-tickets

- 用户批准发布；核心路径末端

### 2026-07-22 implement

- Status: resolved
- `batch_suggest_pending_tags` + `apply_pending_suggestions_selected` + 待审面板「批量 AI 分拣」对话框