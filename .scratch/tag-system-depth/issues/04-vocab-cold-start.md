# 04 — 词表冷启动草案与终审写入

**What to build:** 从草稿词表（如行式一词一行）生成冷启动草案：去重、近义分簇、拟定标准词+别名、分入封闭组并裁到精瘦体量；草案可预览；仅人工终审 commit 后写入标签库与别名。用户侧表现为不必从零想词，又不会未审脏词直接进库。

**Blocked by:** 02 — 标准词·别名·标签归一与待审落库

**Status:** resolved

**Parent:** [spec.md](../spec.md)

## Acceptance criteria

- [x] 输入草稿行可生成草案结构（标准词候选、别名、建议分组、每组裁剪结果）
- [x] 近义分簇可注入假实现以便单测；不要求 CI 调真实大模型
- [x] 未 commit 时标签库标准词/别名不被草案覆盖
- [x] commit 后封闭组具备可用标准词；别名表写入对应关系
- [x] 占位测试词可在清理/替换策略中处理（至少一种明确用户可选或默认可清路径）
- [x] 默认不静默删除用户已有非占位标准词（替换/合并须显式）
- [x] 冷启动草案与 commit 边界有自动化测试

## Comments

### 2026-07-20 to-tickets

- 用户批准 5 票发布；阻塞于 02；完整终审 UI 在 05

### 2026-07-20 implement

- `build_cold_start_draft` / `commit_cold_start_draft` / `merge_draft_into_tag_config`
- 服务层 API；测试覆盖未 commit 不写库 + 占位清理 + 合并保留