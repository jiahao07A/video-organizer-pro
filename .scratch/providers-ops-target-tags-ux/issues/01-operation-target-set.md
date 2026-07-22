# 01 — 操作目标集纯逻辑

**What to build:** 提供可单测的**操作目标集**解析：有列表选中路径时用选中集合；无选中时用当前可见列表全部；工作台场景可与工作范围求交，不得越出范围。不负责 Qt 选中实现，只锁定业务边界，供后续列表与分析入口调用。

**Blocked by:** None — can start immediately

**Status:** resolved

**Parent:** [spec.md](../spec.md)

## Acceptance criteria

- [x] 纯逻辑可单测：非空选中 → 结果等于选中（顺序稳定或文档约定）；空选中 → 结果等于可见列表
- [x] 空可见列表 + 空选中 → 空目标集
- [x] 可选工作范围约束：结果 ⊆ 工作范围；范围外选中被裁掉
- [x] 不依赖勾选列 / `checked_items` 概念
- [x] 文案与 CONTEXT 一致：操作目标集、列表选中、可见列表

## Comments

### 2026-07-21 to-tickets

- 用户批准 7 票发布；主接缝 S1；frontier

### 2026-07-21 implement

- 新增 `core/operation_targets.py`：`resolve_operation_target_paths(selected, visible, *, scope_paths=None)`；规范化键复用 `path_status_key`
- 新增 `tests/test_operation_targets.py` 覆盖选中/可见/空集/scope 裁剪/去重/路径键
- 未改 `gui/` 与 `video_organizer_service`（既有 `checked_paths` 封装留给 02 接线）
- 验收：`python -m pytest tests/test_operation_targets.py -q`