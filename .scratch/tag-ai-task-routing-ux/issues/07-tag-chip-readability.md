# 07 — 标签芯片可读性（浅底 + 组色条）

**What to build:** 素材上的标签芯片统一为**浅底 + 组色左边条/边框**，正文用主题正文色；组色区分氛围/主体/场景/动作/建议。用于**详情面板、列表标签列、卡片标签**三处。疑似错别字用边框/图标/tooltip，不用低对比红字盖在色块上。悬停态保持对比。标签库分栏列表不强制改芯片。深浅主题均可读。

**Blocked by:** None — can start immediately

**Status:** resolved

**Parent:** [spec.md](../spec.md)

## Acceptance criteria

- [x] 详情 TagFlow 芯片为浅底 + 组色条/边，正文为主题文字色
- [x] 列表标签列与卡片标签与详情同一套样式语义（同源 token/组件）
- [x] 组身份仍可由色条区分（五组色映射）
- [x] 错别字不以低对比红字盖色底为唯一手段
- [x] 悬停/选中不把文字对比度打没
- [x] 标签库分栏列表不要求改成素材芯片；芯片样式 token 可单测（主接缝 C）

## Comments

### 2026-07-22 to-tickets

- 用户批准发布；主接缝 C；frontier

### 2026-07-22 implement

- Status: resolved
- `chip_style_tokens` + TagChip 浅底色条；`MaterialTagsColumnDelegate` / Card 绘制同源 token；`test_chip_style_tokens_readable`