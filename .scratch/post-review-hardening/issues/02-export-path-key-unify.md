# 02 — 导出路径键统一（ALE 对齐 FCPX）

**What to build:** 工作台导出 ALE 与导出 FCPX 时，对操作目标路径使用**同一套路径规范化与筛选语义**。避免因路径写法差异漏导，或因空选中语义不一致误导出范围外/全库条目。导出范围须与当前**操作目标集**一致（有列表选中用选中，无选中用当前可见列表；工作台仍不越工作范围）。

**Blocked by:** 01 — 分析缓存：词表变更后不再静默用旧标签

**Status:** resolved

**Parent:** [spec.md](../spec.md)

## Acceptance criteria

- [x] ALE 与 FCPX 对同一组目标路径筛选结果一致（含大小写/斜杠等规范化场景）
- [x] 空列表/未传目标时的行为与「操作目标集」约定一致，有单测或可演示边界
- [x] 工作台导出入口行为与上述规则一致
- [x] 不改变导出字段业务内容（仅路径集合与筛选正确性）

## Comments

### 2026-07-22 implement

- `export_to_ale` 使用 `normalize_work_path` + `is not None`；XMP 同步同语义
- 测试：`test_export_ale_path_normalize_matches_fcpx`