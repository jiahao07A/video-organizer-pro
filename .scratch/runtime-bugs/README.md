# runtime-bugs — 本轮 triage总览

Created: 2026-07-20  
Updated: 2026-07-20（04 主题体系已实现，待验收）

| ID | 标题 | Category | Status | 一句话 |
| --- | --- | --- | --- | --- |
| [01](./issues/01-detail-panel-check-spelling.md) | 选中视频详情崩溃 | bug | **resolved** | service → TagProcessor |
| [02](./issues/02-tags-library-black-ui.md) | 标签库启动时全黑 + Slot | bug | **resolved-partial** | 由 04 补完切换路径 |
| [03](./issues/03-settings-prompt-templates.md) | 设置 Prompt 对齐 | bug | **resolved** | system_prompt + 预览 |
| [04](./issues/04-theme-display-system.md) | 暗/亮主题显示体系 | bug | **resolved**（待验收） | 统一色板 + 切换广播 |

## 04 验收清单

1. light：高级筛选可读  
2. dark：高级筛选可读  
3. light：标签库可读  
4. dark：标签库可读  
5. light→dark：标签库与筛选立即变暗（无需重启）  
6. dark→light：立即变浅  

确认后回复「可以 commit」。