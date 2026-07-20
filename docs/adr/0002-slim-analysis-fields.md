# 分析结果字段瘦身（硬删评分类字段）

Status: accepted（情绪字段部分由 ADR-0003 取代）

为降低大模型输出负担、提高分类/标签/摘要准确率，**分析结果**在瘦身时保留：分类、摘要、情绪、标签（含标签组）。**硬删**并不再生成、展示或导出：构图（composition）、星级（rating）、质量分（quality_score）、代理建议（is_proxy_needed）、标签权重（tag_weights）。旧数据不保留只读兼容；重命名变量与导出中的对应字段一并移除。

**后续修正：** 独立「情绪」字段已由 [ADR-0003](./0003-merge-emotion-into-mood-tags.md) 并入氛围标签组；分析结果现为：分类、摘要、标签（含标签组）。

**Considered options:** 只停 AI 生成、界面只读保留旧值（否决：半留半弃会误导筛选与导出预期）；分阶段只藏 UI（否决：提示词与 schema 仍偏重）。

**Consequences:** 高级筛选不可再依赖已删字段；导出 XMP 评分、`{composition}` 等模板变量需同步下线。