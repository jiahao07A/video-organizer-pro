# 03 — 「开始分析」接操作目标集

**What to build:** 工作台「开始分析」先取**操作目标集**（有列表选中用选中，无选中用当前可见列表全部），再套既有**分析目标集**规则：未分析/失败必跑，已分析默认跳过；「强制重新分析」确认后可纳入已分析并整份覆盖。不再使用勾选列决定分析范围。

**Blocked by:** 02 — 去选择列 + 列表选中接线

**Status:** resolved

**Parent:** [spec.md](../spec.md)

## Acceptance criteria

- [x] 「开始分析」候选路径 = 操作目标集，不再读勾选集合
- [x] 分析目标集规则保持：pending/失败纳入；analyzed 默认排除；force 时纳入
- [x] 全被跳过时诚实提示（无待分析），不伪装整批成功
- [x] 强制重新分析入口与确认、整份覆盖 + 标签归一行为保持既有产品语义
- [x] 有选中时只分析选中中符合规则的；无选中时分析可见列表中符合规则的

## Comments

### 2026-07-21 to-tickets

- 用户批准 7 票发布；主接缝 S2；依赖 02

### 2026-07-21 implement

- Status: claimed → resolved
- `start_analysis` 已用 `_resolve_batch_targets()`（02）；`run_analysis` 再套 `resolve_analysis_target_paths`
- 新增 `tests/test_analyze_operation_pipeline.py` 锁定 S1+S2 组合
- 右键「分析选中项」仅选中、不回退可见全部（与工具栏「开始分析」语义区分）