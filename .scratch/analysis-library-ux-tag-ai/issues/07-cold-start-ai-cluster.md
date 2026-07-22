# 07 — 冷启动 AI 分簇

**What to build:** 词表冷启动使用 **冷启动 AI 分簇** 辅助近义分簇并拟定标准词、别名与分组建议；输出仍是可编辑草案；人工终审 commit 后才写入标签库；取消/关闭不写库。分簇实现可注入（规则或假 AI），CI 不依赖真实大模型。

**Blocked by:** None — can start immediately（可与 01–03、06、08 并行；依赖既有冷启动草案/commit 边界）

**Status:** resolved

**Parent:** [spec.md](../spec.md) · ADR-0004

## Acceptance criteria

- [ ] 冷启动可走 AI（或可注入）分簇生成草案：标准词候选、别名、分组建议
- [ ] 草案在 commit 前可人工编辑
- [ ] 取消或关闭对话框不写库（可测）
- [ ] 终审 commit 仍走既有写入边界；不自动删除用户未确认的非占位标准词（除非用户显式选择既有替换策略）
- [ ] 标签库「词表冷启动」入口可使用增强分簇

## Comments

### 2026-07-21 to-tickets

- 用户批准发布；主接缝 C 之一；frontier

### 2026-07-21 implement

- Status: claimed → resolved
- 主会话批量实现 analysis-library-ux-tag-ai

### 2026-07-21 fix: real AI cold-start

- `AIHandler.cluster_cold_start_words` 真实调用 tag_generation 模型做近义分簇
- 解析硬过滤 + 失败降级规则分簇；UI 预览显示「分簇说明」
- 测试：`tests/test_cold_start_ai.py`
