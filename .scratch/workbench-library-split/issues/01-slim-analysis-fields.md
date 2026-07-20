# 01 — 分析结果字段瘦身（硬删）

**What to build:** 分析结果不再包含构图、星级、质量分、代理建议、标签权重：分析提示与写入只保留分类、摘要、情绪、标签；详情、导出、重命名模板不再使用已删字段；旧数据无只读兼容入口。用户侧表现为分析更轻、界面与导出与真实能力一致。

**Blocked by:** None — can start immediately

**Status:** resolved（实现完成，待 GUI 验收）

**Parent:** [spec.md](../spec.md) · ADR-0002

## Acceptance criteria

- [x] 分析提示词/期望 JSON 结构不含 composition、rating、quality_score、is_proxy_needed、tag_weights
- [x] 分析结果写入与读取路径不再依赖上述字段；产品语义为硬删
- [x] 详情面板不再展示构图、星级/质量、代理建议；标签展示不依赖权重
- [x] 重命名变量与导出（含 XMP 评分等）不再使用已删字段
- [x] 文本搜索等路径不再拼接 composition 等已删字段
- [x] 服务层测试：提示词/写入 payload 含保留字段、不含已删字段

## Comments

### 2026-07-20 to-tickets

- 批准发布；可与 02 并行开工

### 2026-07-20 implement

- 主会话实现；pytest `test_slim_analysis_fields` 通过