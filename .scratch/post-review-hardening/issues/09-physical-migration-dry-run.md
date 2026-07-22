# 09 — 物理整理模拟预览再确认

**What to build:** 「自动分类整理」在真正搬文件前，先展示**模拟预览**清单（源路径 → 目标路径，及条数摘要）。用户取消则零搬迁；确认后走后台执行（依赖 06）。心智对齐「模拟重命名 → 应用重命名」，属高风险操作前的可查看阶段。

**Blocked by:** 08 — 分析成功默认不静默写 XMP

**Status:** resolved

**Parent:** [spec.md](../spec.md)

## Acceptance criteria

- [x] 选择目标根目录后先出源→目标清单，未确认前磁盘文件不移动
- [x] 用户取消模拟：零文件变更、库路径不变
- [x] 用户确认后执行结果与清单一致（命名冲突策略可解释）
- [x] 执行仍走后台且有进度（与 06 一致）

## Comments

### 2026-07-22 implement

- `plan_physical_migration` + UI 预览确认 + 后台 `execute_physical_migration`
- 测试：`test_plan_physical_migration_no_disk_move`