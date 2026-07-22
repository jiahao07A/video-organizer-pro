# 08 — 近义巡检

**What to build:** 对现有标签库做 **近义巡检**，列出可能重复或应合并为「标准词 + 别名」的建议清单；合并须人工确认。确认后：被合并词挂为保留词别名，并将已标注视频上的旧标准词归一到保留词。禁止静默合并。AI/规则可注入，CI 不依赖真实大模型。

**Blocked by:** None — can start immediately（可与 01–03、06、07 并行）

**Status:** resolved

**Parent:** [spec.md](../spec.md) · ADR-0004

## Acceptance criteria

- [ ] 可对当前标准词（及可选别名/用量）生成合并建议清单
- [ ] 未确认时不修改词表与视频标签（可测）
- [ ] 确认合并后：保留词仍为标准词；被合并词成为别名；相关视频标签归一到保留词
- [ ] 标签库有近义巡检入口与确认交互
- [ ] 不提供「无人确认的自动合并」

## Comments

### 2026-07-21 to-tickets

- 用户批准发布；主接缝 C 之一；frontier

### 2026-07-21 implement

- Status: claimed → resolved
- 主会话批量实现 analysis-library-ux-tag-ai
