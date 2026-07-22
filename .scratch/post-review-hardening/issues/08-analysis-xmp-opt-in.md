# 08 — 分析成功默认不静默写 XMP

**What to build:** 分析任务成功落库后，**默认不再**自动在磁盘写入 XMP 侧车。用户可通过设置开关打开「分析成功后自动同步 XMP」，或继续使用工作台手动「同步元数据」。默认行为须在设置或首次相关提示中可理解。不削弱手动同步（05 后台化）能力。

**Blocked by:** 07 — 批量换标签按操作目标集

**Status:** resolved

**Parent:** [spec.md](../spec.md) · ADR-0005（分析任务边界）

## Acceptance criteria

- [x] 默认配置下：仅分析成功后，磁盘上不因分析路径自动新增/覆盖 XMP
- [x] 用户打开自动同步开关后，分析成功可再写 XMP（行为可测）
- [x] 手动「同步元数据」始终可用
- [x] 设置文案说明默认与风险；不与「强制重新分析」语义冲突

## Comments

### 2026-07-22 implement

- `processing.auto_sync_xmp_after_analysis` 默认 False；设置页勾选
- `_persist_analysis_success` 条件写 XMP
- 测试：`test_persist_success_default_no_auto_xmp`；更新 tagging_bugs XMP 失败测