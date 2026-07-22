# Spec: 分析任务三层重试 + 双进度与行临时态

Status: ready-for-agent  
Created: 2026-07-22  
Feature-slug: `analysis-retry-progress`  
ADRs: [0005](../../docs/adr/0005-analysis-retry-and-progress.md)（主）, [0004](../../docs/adr/0004-tag-normalize-hard-filter-pending.md)（落库归一边界不变）  
Glossary: 见根目录 `CONTEXT.md`（分析任务、分析阶段、调用层重试、单条层重试、批次补跑、分析进度、分析行临时态、取消分析、分析目标集、强制重新分析、分析结果、标签归一、操作目标集、工作台）

---

## Problem Statement

剪辑师在工作台对分析目标集执行「开始分析」或「强制重新分析」时，仍会遇到：

1. **瞬时失败脆**：API 空 body、JSON 解析失败、超时、429/5xx 等只失败一次就整条挂掉；客户端底层重试不足以覆盖应用层可识别的瞬时错误。  
2. **批次失败要手点**：首轮失败后需用户再次开分析才能重跑失败项，体感「重试不健全」。  
3. **进度不诚实、不清晰**：只有「完成 x/y」式粗进度；并行时不知道哪条在跑、卡在抽帧还是调 AI；自动重试/补跑时更分不清「重来一轮」还是「还在原任务」。  
4. **无法安全中止**：长任务跑飞时缺少协作式取消；若取消后仍自动补跑则更糟。

用户要的是：**分析任务内自动且有上限的三层重试**，以及 **总进度 + 子进度 + 列表行临时态**，并在高级设置中可调，且**不扩大到重命名等其它长任务**。

## Solution

1. **调用层重试**：同一次 AI 请求内对 T1 瞬时错误退避再试（默认额外 2 次，共 3 次尝试）。  
2. **单条层重试**：单条视频整段流程失败后，在本轮内再完整尝试（默认每条最多 2 次完整尝试）。  
3. **批次补跑**：首轮结束后对仍失败集合自动再跑 **1** 轮（默认开，高级可关）；取消后不得再开补跑。  
4. **分析进度**：总进度条覆盖整次任务且补跑不清零；子进度对应当前轮，补跑段单独标注且从 0 再计；按终态文件数推进。  
5. **分析行临时态**：列表显示分析中·阶段 / 重试中 / 成功 / 失败等，**仅 UI**，不写库 `status`。  
6. **取消分析**：协作式停止排队与补跑；保留已成功；进行中尽量收尾。  
7. **设置（高级）**：次数与「自动批次补跑」可配，默认采用 ADR-0005 的 P 上限。

## User Stories

### 调用层重试

1. As a 剪辑师, I want 分析时遇到超时或连接重置能自动再试, so that 网络抖动不会轻易浪费整条素材。  
2. As a 剪辑师, I want 遇到 HTTP 429/502/503 时自动退避再试, so that 限流与网关抖动可自愈。  
3. As a 剪辑师, I want 遇到空 body 或 JSON 解析失败时自动再试, so that 供应商偶发坏响应不直接整条失败。  
4. As a 剪辑师, I want 鉴权失败（401/403）不要空转重试, so that 我能立刻知道要改模型供应商配置。  
5. As a 剪辑师, I want 调用层重试有次数上限, so that 费用与时间不会失控。  
6. As a 维护者, I want 可重试判定可单测, so that 改错误分类不会靠猜。

### 单条层重试

7. As a 剪辑师, I want 单条视频整段流程失败后在本轮内再试有限次, so that 抽帧毛刺或单次链路失败有第二次机会。  
8. As a 剪辑师, I want 单条重试次数有上限（默认共 2 次完整尝试）, so that 不会对同一文件无限打 API。  
9. As a 剪辑师, I want 文件不存在/打不开时不要单条空转重试, so that 失败原因清晰。  
10. As a 剪辑师, I want 行上能看到「重试中」或等价临时态, so that 我知道系统还在处理而非卡死。  
11. As a 维护者, I want 单条层次数与调用层次数语义分离可测, so that 两层不会被实现成同一个计数器。

### 批次补跑

12. As a 剪辑师, I want 首轮结束后失败项自动再跑一轮, so that 我不必手动再点开始分析。  
13. As a 剪辑师, I want 批次补跑最多 1 轮, so that 失败集不会无限循环。  
14. As a 剪辑师, I want 补跑只针对仍失败项而不重跑已成功项, so that 不重复烧已成功的 API。  
15. As a 剪辑师, I want 能在高级设置关闭自动批次补跑, so that 我可以控费用。  
16. As a 剪辑师, I want 关闭补跑时仍保留调用层与单条层重试, so that 基础稳健性不丢。  
17. As a 剪辑师, I want 取消分析后不再启动批次补跑, so that 中止语义完整。  
18. As a 剪辑师, I want 补跑段进度单独标注, so that 我不把补跑误当成任务重开。  
19. As a 维护者, I want 批次补跑条件可单测, so that 「已补跑过 / 已取消 / 设置为关」不会漏。

### 分析进度（总/子）

20. As a 剪辑师, I want 看到总进度条反映整次分析任务, so that 我知道整体走了多远。  
21. As a 剪辑师, I want 进入补跑时总进度条不清零, so that 我不以为任务被重置。  
22. As a 剪辑师, I want 子进度条对应当前轮并从补跑的 0 再走, so that 当前轮进展清晰。  
23. As a 剪辑师, I want 首轮分母等于分析目标集条数, so that 完成数对应文件数。  
24. As a 剪辑师, I want 单条层重试不增加进度分母, so that 百分比不会因为重试虚高。  
25. As a 剪辑师, I want 补跑轮分母等于本轮失败集条数, so that 子进度有意义。  
26. As a 剪辑师, I want 进度按终态文件数推进而非请求次数, so that 并行时也不虚报。  
27. As a 剪辑师, I want 状态文案包含分析阶段（如抽帧、调 AI）, so that 我知道卡在哪一步。  
28. As a 剪辑师, I want 并行时能感知进行中条数, so that 多 worker 时不瞎。  
29. As a 剪辑师, I want 无待分析时文案诚实（非假 100% 分析成功）, so that 与既有分析目标集行为一致。  
30. As a 维护者, I want 进度快照由事件归约且可单测, so that UI 只是展示层。

### 分析行临时态

31. As a 剪辑师, I want 列表行在分析任务中显示临时态（分析中·阶段 / 重试中 / 成功 / 失败）, so that 我能扫一眼定位问题行。  
32. As a 剪辑师, I want 临时态不写入素材库持久 status, so that 重启后不会留下 analyzing 脏状态。  
33. As a 剪辑师, I want 任务结束或取消后行展示回到库内状态, so that 与筛选/分析目标集一致。  
34. As a 剪辑师, I want 成功/失败落库后列表最终与库一致, so that 我能继续模拟重命名。  
35. As a 维护者, I want 行临时态与库 status 边界可测, so that 实现不会误 upsert analyzing。

### 取消分析

36. As a 剪辑师, I want 分析进行中可取消, so that 我能停掉误开的大批量。  
37. As a 剪辑师, I want 取消后不再提交新的单条处理, so that 费用尽快停下。  
38. As a 剪辑师, I want 取消后不进入批次补跑, so that 中止不会被自动二段推翻。  
39. As a 剪辑师, I want 已成功落库的结果在取消后仍保留, so that 已花的 API 不白费。  
40. As a 剪辑师, I want 未完成项有可理解的取消/失败原因, so that 我知道哪些没跑完。  
41. As a 维护者, I want 协作式取消标志可测, so that 不依赖强杀线程。

### 设置与默认

42. As a 剪辑师, I want 默认即启用 ADR-0005 的 P 上限与自动补跑, so that 开箱稳健。  
43. As a 剪辑师, I want 在设置高级区调整调用层/单条层次数与补跑开关, so that 我能控费用。  
44. As a 剪辑师, I want 设置保存后对**下一次**分析任务生效, so that 进行中任务不被中途改规则搅乱（或文档明确「仅下次」）。  
45. As a 维护者, I want 默认值与设置键有单一读取点, so that UI 与服务层不一致。

### 与既有分析语义共存

46. As a 剪辑师, I want 分析目标集规则不变（失败默认可进、已分析默认跳过、强制覆盖）, so that 旧行为不回退。  
47. As a 剪辑师, I want 强制重新分析仍需确认且成功整份覆盖并经标签归一, so that ADR-0004 不被绕过。  
48. As a 剪辑师, I want XMP 等副作用失败不推翻已成功分析落库, so that 侧车问题不等于分析失败。  
49. As a 剪辑师, I want 全部失败/部分成功时任务成功标志诚实, so that 弹窗不撒谎。  
50. As a 剪辑师, I want 失败摘要仍可见（原因/文件名）, so that 与既有可观测性一致并增强。  
51. As a 剪辑师, I want 本特性不改变工作范围/操作目标集解析, so that 选哪些视频进任务仍按原规则。  
52. As a 维护者, I want 回归不破坏既有 analysis_targets / 强制重分析缓存绕过等测试, so that 主链路不回退。

### 范围边界（用户可感知）

53. As a 剪辑师, I want 重命名任务不套用本套三层重试与双进度, so that 行为边界清晰。  
54. As a 剪辑师, I want 文档与界面使用 CONTEXT 词汇（分析任务、批次补跑、分析行临时态等）, so that 概念不混。

## Implementation Decisions

### 架构与 ADR

- 遵循 **ADR-0005** 全文决策；产品词一律 `CONTEXT.md`。  
- 遵循 **ADR-0004**：分析成功落库前标签归一不变。  
- 范围 **R1**：仅分析任务（开始分析 / 强制重新分析）。  
- 不把 `analyzing` / `retrying` 写入库 `status`。

### 接缝（已确认）

**主接缝 — 分析任务策略纯逻辑（优先单测）**

- 合并「重试策略 + 进度归约」于同一模块边界（避免两套分叉真相）。  
- 提供：  
  - `is_retriable(error) -> bool`（T1）  
  - `should_retry_call(attempt_a, max_extra) -> bool`  
  - `should_retry_item(attempt_b, max_attempts) -> bool`  
  - `should_batch_rerun(failed_count, rerun_done, enabled, cancelled) -> bool`  
  - 进度归约：消费事件 → 进度快照（总/子、文案、行临时态 map）  
- 事件至少覆盖：任务开始、轮次开始（first|rerun）、条目阶段、条目终态、任务取消。  
- 默认上限：A 额外 2、B 共 2 次、C 1 轮；由配置注入，默认值写死为 P。

**编排接缝 — 分析任务服务编排**

- 扩展既有「跑分析」生命周期：首轮 →（可选）批次补跑；贯穿取消标志。  
- 单条处理内：调用层重试包住 AI 请求；单条层重试包住整段流程；不可恢复错误短路。  
- 终态成功：upsert 为 analyzed（及既有字段）；XMP 副作用失败不推翻成功计数。  
- 终态失败：status=failed，保留 last_error/原因；计入失败集供补跑。  
- 取消：不再 `submit` 新任务；不 `should_batch_rerun`；已成功保留。  
- 并行：`max_workers` 仍可用；进度与行态经线程安全回调抛到 UI 线程。

**薄适配 — Worker / 工作台 UI**

- 双进度条：总进度 + 子进度；补跑段文案单独标。  
- 状态栏/标签：阶段 + 轮次 + 进行中条数。  
- 列表：按 path 应用分析行临时态装饰；任务结束清理临时态并刷新库状态。  
- 取消分析控件：任务进行中可点；结束后禁用。  
- 设置页高级：A/B 次数、自动批次补跑开关；读写与服务默认一致。

### 默认与配置（概念键，非文件路径）

- `processing.analysis_retry.call_extra_attempts` 默认 2  
- `processing.analysis_retry.item_max_attempts` 默认 2  
- `processing.analysis_retry.batch_rerun_enabled` 默认 true  
- `processing.analysis_retry.batch_rerun_max_rounds` 默认 1（本轮产品固定 1，设置至少能关；若暴露轮数不得超过 1 除非另开 ADR）  
- 退避：调用层指数退避（如 1s→2s→4s）可实现为纯函数；单测可注入假时钟/零延迟。

### 与现有进度回调的关系

- 旧 `on_progress(current, total)` 可保留兼容，但 L3 以**结构化进度快照/事件**为准；UI 优先消费快照。  
- `AnalysisWorker` 成功语义继续诚实（全失败/部分失败为失败标志等既有约定）。

### 实现顺序建议（供 to-tickets）

1. 策略纯逻辑 + 单测（重试判定、次数、进度归约、取消不再 C）  
2. 服务编排接入 A/B/C + 取消标志 + 失败原因  
3. Worker/UI 双条、行临时态、取消按钮  
4. 设置高级项 + 持久化  
5. 端到端人测清单（可选票）

## Testing Decisions

### 好测试的标准

- 只断言**外部行为**与纯逻辑契约：给定错误/事件/配置 → 是否重试、快照字段、是否补跑。  
- 不绑定具体 Qt 控件类名或私有字段布局。  
- 不依赖真实大模型/真实视频网络（编排测注入假 AI/假抽帧）。  
- 不把实现细节（睡眠秒数绝对值）当主断言；可测「调用了 N 次」与「延迟序列可注入」。

### 测什么

| 模块/接缝 | 要点 |
| --- | --- |
| 策略纯逻辑 | T1 可重试/不可重试；A/B 上限；C 条件（失败数、已跑过、enabled、cancelled）；进度分母与补跑子进度归零；行临时态 map 更新与清理 |
| 服务编排（可 mock） | 瞬时失败后 AI 被再调；不可恢复只调一次；失败集补跑一轮；取消后无补跑；成功+XMP 失败仍 succeeded |
| Worker 成功语义 | 与进度/失败摘要共存的既有断言可回归 |
| 设置读取 | 默认 P；关闭补跑后 should_batch_rerun=false |

### 既有 prior art

- `tests/test_analysis_targets.py`、`tests/test_analysis_flow_service.py`、`tests/test_analysis_tagging_bugs.py`、`tests/test_analyze_operation_pipeline.py`  
- 纯逻辑风格：`tests/test_tag_normalize.py`  
- 本特性新增宜并列：`test_analysis_retry_policy.py`（名可调整）专测主接缝

## Out of Scope

- 重命名 / 模拟整理 / 导出 / 物理迁移的同等重试与双进度框架  
- ETA、预估剩余时间、速度曲线  
- 将 analyzing 持久化入库或进高级筛选状态维  
- 批次补跑超过 1 轮，或默认改为「必须用户确认才补跑」（与已接受 ADR-0005 冲突；若改须新 ADR）  
- 更换模型供应商运行时热切换中断本轮任务（仍可按既有：本轮不中断、下批再生效）  
- 人脸识别/人物库增强、标签库 AI、词表产品改版  
- 解决供应商内容质量（色偏抽帧等另案；本 spec 不包含色域修复，除非分析路径回归测需要）

## Further Notes

- 术语以 `CONTEXT.md` 为准；实现注释与用户可见字符串优先中文产品词。  
- 费用敏感：文档/设置文案可提示「自动补跑会增加 API 调用」。  
- 与 `.scratch/analysis-tagging-bugs` 正交：侧车/Worker 诚实/失败摘要等已落地的修复应保留，本特性在其上叠加重试与进度。  
- 下一技能：`/to-tickets` 将本 spec 拆为 tracer-bullet issues（建议 blockers：纯逻辑 → 编排 → UI → 设置）。  
- Seams 已于 2026-07-22 经维护者确认（「接缝 OK」）。

---

## Seams（定稿摘要）

1. **主**：分析任务策略纯逻辑（重试 + 进度事件归约）  
2. **编排**：分析任务服务生命周期（首轮/补跑/取消/落库边界）  
3. **薄适配**：Worker + 工作台双进度与行临时态 + 设置

Status: **ready-for-agent**