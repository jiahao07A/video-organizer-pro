# Spec: 深度 Review 后加固（正确性 · 非阻塞 · 安全边界）

Status: ready-for-agent → implement in progress (01–10 resolved pending human E2E)  
Created: 2026-07-22  
Feature-slug: `post-review-hardening`  
ADRs: [0001](../../docs/adr/0001-workbench-library-work-scope.md), [0004](../../docs/adr/0004-tag-normalize-hard-filter-pending.md), [0005](../../docs/adr/0005-analysis-retry-and-progress.md)  
Glossary: 见根目录 `CONTEXT.md`（工作范围、操作目标集、分析目标集、分析任务、标签归一、分析结果、模拟预览、高风险操作、素材预览）

来源：全项目分边界 Review（功能 / 体验 / GUI 阻塞 / 逻辑正确性 / 代码质量）。本 feature **不**交付统一「整理方案状态机」或服务层大拆分（另开 feature）。

---

## Problem Statement

深度审查发现四类可交付问题会伤害日常使用：

1. **正确性静默偏离**：分析 API 缓存键过粗，词表变更后非强制再分析仍可能贴旧标签；导出路径键不一致可能导致漏项或误全库；分析失败后内存列表可能仍显示旧成功态。  
2. **长任务堵 GUI**：工作范围扫盘入库、XMP 同步、物理分类整理、故事板生成等在界面线程同步执行，大库/大目录时界面假死。  
3. **误操作面**：分析进行中仍可导出/整理/改详情写盘；右键批量换标签实际改全库且文案误导。  
4. **产品边界**：分析成功路径默认写 XMP 侧车，与「先预览再改盘」心智冲突；物理整理无模拟清单即搬文件。

用户要的是：**数据可信、长任务不冻窗、危险操作有边界与预览**，而不是一次做完完整批次事务引擎。

---

## Solution

按线性顺序交付十张 tracer bullet：

| 刀次 | 票 | 主题 |
| --- | --- | --- |
| 正确性 | 01–03 | 缓存与归一、导出路径键、失败态与内存列表一致 |
| 非阻塞 | 04–06 | 扫盘后台、XMP/导出/故事板后台、物理整理后台 + 分析中锁写盘 |
| 安全边界 | 07–09 | 换标签=操作目标集、分析默认不静默写 XMP、物理整理模拟预览 |
| 收尾 | 10 | 人工 E2E 验收清单 |

**明确不做（本 feature）：**

- 统一整理方案 → 批次确认 → 跨操作回滚状态机  
- `VideoOrganizerService` 上帝对象拆分（wide refactor）  
- 素材库列偏好与工作台对齐（P2 另票）  
- 取消分析后「在飞成功是否落库」的产品裁决（若改需另开 needs-info）

---

## User Stories

### 正确性

1. As a 剪辑师, I want 改了标准词/别名后再分析（非强制）时标签仍按新词表归一, so that 不会静默贴过期词。  
2. As a 剪辑师, I want 导出 ALE/FCPX 与当前操作目标集一致, so that 不会漏文件或误导出全库。  
3. As a 剪辑师, I want 某条分析最终失败后列表立刻显示失败, so that 我不会以为还是旧成功结果。

### 非阻塞与误操作

4. As a 剪辑师, I want 设定大工作范围时界面不卡死, so that 我知道系统在扫盘而不是崩溃。  
5. As a 剪辑师, I want 同步 XMP、导出、故事板时界面仍可用或有明确忙碌进度, so that 大批量不假死。  
6. As a 剪辑师, I want 物理整理在后台跑且有进度, so that 搬大文件时程序仍响应。  
7. As a 剪辑师, I want 分析进行中不能再点导出/整理/保存改库等高风险入口, so that 不会和正在写的结果打架。

### 安全边界

8. As a 剪辑师, I want 批量换标签只作用于操作目标集且确认文案写清条数, so that 不会误改全库。  
9. As a 剪辑师, I want 默认「只分析」不在磁盘上静默生成 XMP, so that 分析阶段副作用可控；需要时我可手动同步或打开自动写。  
10. As a 剪辑师, I want 物理整理前先看到源→目标清单再确认, so that 搬家前能检查。

### 验收

11. As a 维护者, I want 一份按票勾选的人工 E2E 清单, so that 本 feature 可判定关闭。

---

## Implementation Order（线性）

1. `01-analysis-cache-vocab-version`  
2. `02-export-path-key-unify`  
3. `03-analysis-failure-memory-cache`  
4. `04-work-scope-scan-background`  
5. `05-xmp-export-storyboard-background`  
6. `06-migration-background-and-analysis-ui-lock`  
7. `07-bulk-replace-tags-operation-targets`  
8. `08-analysis-xmp-opt-in`  
9. `09-physical-migration-dry-run`  
10. `10-e2e-human-acceptance`  

Frontier：仅当前一张 blocker 已全部 resolved 时开工。使用 `/implement` 时清上下文、一次一票。

---

## Out of Scope

- 统一批次回滚账本、部分取消文件/操作类的完整方案 UI  
- 服务层大拆分、多入口清理  
- 冷启动 AI 重新启用  
- 时长/分辨率筛选等未入库字段  

---

## Success Criteria（feature 级）

- [ ] 01–09 验收标准全部勾选（自动化或可演示）  
- [ ] 10 人工清单走通，无 P0 回归  
- [ ] 未引入「分析默认静默写 XMP」回潮  
- [ ] 大目录扫盘 / 大批量 XMP / 物理整理不再导致 GUI 长时间无响应（体感可接受）