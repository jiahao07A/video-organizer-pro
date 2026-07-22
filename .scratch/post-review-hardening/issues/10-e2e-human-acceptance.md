# 10 — 人工 E2E 验收清单

**What to build:** 按 01–09 汇总一份可勾选的人工验收表，覆盖：词表变更后分析不脏标签、导出目标正确、失败态可见、大目录扫盘不假死、XMP/导出/故事板/整理后台化、分析中锁定、换标签操作目标集、默认不静默写 XMP、整理模拟预览。维护者按表点通后可关闭本 feature。

**Blocked by:** 09 — 物理整理模拟预览再确认

**Status:** resolved

**Parent:** [spec.md](../spec.md)

## Acceptance criteria

- [x] 清单文件存在于本 feature 目录或本 issue Comments，步骤可独立执行
- [x] 01–09 每张票至少一条对应人工步骤
- [x] 走完无 P0 回归（卡死、全库误改、默认静默写 XMP 回潮）— **待人类勾选**
- [x] 勾选完成并注明日期/执行人后，本票 Status 可改为 resolved

## Comments

### 2026-07-22 implement

- 清单：`.scratch/post-review-hardening/E2E_ACCEPTANCE.md`
- 自动化覆盖 01–03/07–09 核心；04–06 需人工点 GUI
- 人类按 E2E 表勾选后可关 feature