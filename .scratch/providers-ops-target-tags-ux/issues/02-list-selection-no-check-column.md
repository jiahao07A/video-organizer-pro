# 02 — 去选择列 + 列表选中接线

**What to build:** 工作台与素材库去掉列表「选择」勾选列；批量操作（移出工作范围、取消入库、累加到工作范围、导出、模拟整理/重命名、物理整理等）统一走**操作目标集**（01）：有列表选中用选中，无选中用当前可见列表全部。交互对齐资源管理器常用子集：单击、Ctrl、Shift、Ctrl+A、点空白取消选中；列表与卡片同一套语义。破坏性操作确认框写明条数与产品语义。列显示/列宽偏好在无选择列后仍可用。

**Blocked by:** 01 — 操作目标集纯逻辑

**Status:** resolved

**Parent:** [spec.md](../spec.md)

## Acceptance criteria

- [x] 列表视图无「选择」勾选列；模型不再以勾选集合作为批量主依据
- [x] 有列表选中时批量操作只作用于选中路径；无选中时作用于当前可见列表全部（受筛选）
- [x] 工作台批量结果不越过工作范围
- [x] 工作台与素材库列表/卡片：ExtendedSelection 行为可用（Ctrl/Shift/Ctrl+A）；点空白可清除选中（实现允许的合理方式）
- [x] 移出工作范围 / 取消入库确认文案含条数与语义（移出 vs 取消入库）
- [x] 素材库「累加到工作范围」使用操作目标集
- [x] 旧列偏好中含「选择」列时不崩溃、可忽略或迁移

## Comments

### 2026-07-21 to-tickets

- 用户批准 7 票发布；依赖 01

### 2026-07-21 implement

- 去掉 `VideoTableModel` 的 `COL_CHECK` / `checked_items` / `CheckStateRole`；列序重排为 7 列（#…状态）
- 工作台 / 素材库批量入口改走 `_get_selected_paths` + `_get_visible_paths` → `service.resolve_operation_target_paths`（对接 `core.operation_targets`）
- 服务层扩展 `resolve_operation_target_paths` / `get_videos_for_operation`：新参 selected+visible+scope；旧单参 checked 语义保留（兼容 `test_ops_filter_scope`）
- 交互：ExtendedSelection + Ctrl+A shortcut + 点空白 clearSelection 事件过滤器
- 列偏好 `migrate_table_column_prefs`：旧 8 列键迁移（丢弃选择列、索引 -1），越界忽略
- 测试：`test_operation_targets` + `test_ops_filter_scope` 通过