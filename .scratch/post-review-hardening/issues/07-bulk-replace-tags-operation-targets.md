# 07 — 批量换标签按操作目标集

**What to build:** 工作台（及若存在的同类入口）「批量替换标签」只作用于当前**操作目标集**：有列表选中 → 选中集合；无选中 → 当前可见列表。确认框写明将处理的条数与语义。禁止在用户只选了一条时静默改全库。

**Blocked by:** 06 — 物理整理后台化 + 分析中锁定写盘操作

**Status:** resolved

**Parent:** [spec.md](../spec.md)

## Acceptance criteria

- [x] 仅选中 1 条时，只影响该目标集内相关视频上的旧标签替换
- [x] 无选中时作用于当前可见列表，确认文案含条数
- [x] 不再出现「文案写所有视频且无视选中」的误导行为
- [x] 替换后仍走标签归一（封闭组不落库外词等既有规则）

## Comments

### 2026-07-22 implement

- `bulk_replace_tags(..., selected_paths=)`；工作台确认文案含条数
- 测试：`test_bulk_replace_respects_selected_paths`