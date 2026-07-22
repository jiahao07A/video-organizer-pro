# 01 — 分析目标集 + 强制重新分析

**What to build:** 点「开始分析」时按**分析目标集**决定队列：未分析/失败必跑，已分析默认跳过；提供显式「强制重新分析」（确认后整份覆盖分类/摘要/标签，经标签归一）。禁止「已入库 = 已处理」导致秒完成。进度与完成文案对实际尝试分析的条目诚实；失败状态可再次进入目标集。用户可在测试工作范围上真正跑完分析并得到可重命名的字段。

**Blocked by:** None — can start immediately

**Status:** resolved

**Parent:** [spec.md](../spec.md)

## Acceptance criteria

- [ ] 目标集解析可单测：pending/失败纳入；analyzed 默认排除；force 时纳入已分析；禁止仅因「路径已在库」而排除
- [ ] 「开始分析」只处理目标集；全被跳过时提示「没有待分析视频」（或等价），不伪装整批分析成功
- [ ] 有勾选时目标集 ⊆ 勾选且 ∈ 工作范围；未勾选时 = 范围内符合规则的全部
- [ ] UI 提供强制重新分析入口；执行前确认；成功后 category/summary/tags/tag_groups 整份覆盖并经标签归一
- [ ] 分析失败条目状态可识别，且默认再次进入目标集
- [ ] 成功后状态为已分析，详情/列表可见分类、摘要、标签，足以支撑既有模拟/应用重命名

## Comments

### 2026-07-21 to-tickets

- 用户批准 9 票发布；主接缝 A+B；frontier 优先

### 2026-07-21 implement

- Status: claimed → resolved
- 主会话批量实现 analysis-library-ux-tag-ai
