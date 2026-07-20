# 03 — 工作台操作边界 ⊆ 工作范围

**What to build:** 工作台上的分析、模拟整理、应用重命名、导出等操作，默认目标集合始终落在当前工作范围内。列表勾选则在范围内再缩小；未勾选 = 范围内全部。禁止旧行为「未勾选则对全库动手」。

**Blocked by:** 02 — 工作范围：替换、扫盘入库、工作台只显示范围

**Status:** resolved（实现完成，待 GUI 验收）

**Parent:** [spec.md](../spec.md) · ADR-0001

## Acceptance criteria

- [x] 无勾选时，分析/重命名/导出等目标 = 工作范围内素材，而非全库
- [x] 有勾选时，目标 ⊆ 勾选集且 ⊆ 工作范围
- [x] 工作范围为空时，操作给出明确警告且不扫描/不改动全库
- [x] 服务层或等价门面可测：解析出的目标路径集合满足 ⊆ 范围

## Comments

### 2026-07-20 implement

- `resolve_operation_target_paths` / `get_videos_for_operation`