# Spec: 标签库真 AI · 任务模型路由 · Prompt 中心 · 标签芯片可读性

Status: ready-for-agent  
Created: 2026-07-22  
Feature-slug: `tag-ai-task-routing-ux`  
ADRs: [0004](../../docs/adr/0004-tag-normalize-hard-filter-pending.md), [0006](../../docs/adr/0006-task-model-routing.md)  
Glossary: 见根目录 `CONTEXT.md`（模型供应商、当前供应商、任务模型路由、待审词 AI 建议、批量待审 AI 分拣、标准词 AI 助手、近义巡检、冷启动 AI 分簇、标签归一、标准词、别名、待审词、标签移动、标签组、工作台、素材库、卡片信息）

---

## Problem Statement

标签库虽已有「AI 建议 / 近义巡检 / 冷启动」入口，但**待审与近义巡检默认走本地规则**，多数时候并未真正调用大模型，用户感知为「假 AI」。冷启动 AI 效果不达标，产品上应继续停用。日常审词仍缺两类省时能力：**批量**处理待审建议，以及对**单个标准词**给别名/改组建议。

设置侧只能「整包切换当前供应商」，无法按 AI 功能跨供应商、跨价位选模型；Prompt 模板页只能改分析全局 system 并预览分析拼装，**标签库 AI 的 prompt 与各组 local_prompt 无法在同一处查看与修改**，与「真正管理全部 prompt」不符。

工作台 / 素材库 / 详情上的标签芯片把组色当实心背景，文字色未保证对比度，疑似错别字还强行红字盖在色块上，**标签颜色不具可读性**（用户主诉）。

## Solution

1. **标签库真 AI（核心）**  
   - **待审词 AI 建议**、**近义巡检**：默认走大模型（经任务模型路由）；失败/无 Key 时规则降级；**一律人工确认后才写库**（ADR-0004）。  
   - **冷启动 AI 分簇**：本轮保持停用，仅规则分簇 + 人工终审。  
   - 新增 **批量待审 AI 分拣**：对队列生成建议清单 → 用户多选 →「应用选中」才写库。  
   - 新增 **标准词 AI 助手**：对单个标准词建议**别名 + 改组**；确认后挂别名或标签移动；**不做**「并入另一标准词」（合并仍走近义巡检）。

2. **任务模型路由（ADR-0006）**  
   六槽各自选供应商 + 模型名；当前供应商仅为未配置功能的默认回退。

3. **设置 = 全部可编辑 Prompt 中心**  
   分析全局 system、各标签组 local_prompt、待审/近义/标准词助手模板均可查看与修改，并提供「实际发出」预览（分析沿用既有拼装；标签库 AI 按功能模板预览）。冷启动模板本轮可隐藏。

4. **标签芯片可读性**  
   素材上的标签统一为 **浅底 + 组色左边条/边框**，正文用主题正文色；用于详情、列表标签列、卡片标签。标签库分栏列表不强制改芯片。错别字用边框/图标标示，不用低对比红字盖色块。

## User Stories

### 待审词真 AI 与批量分拣

1. As a 剪辑师, I want 对待审词点「AI 建议」时默认调用大模型, so that 建议基于语义而不只是子串规则。
2. As a 剪辑师, I want 模型不可用或失败时仍看到规则降级建议并标明来源, so that 我知道不是静默空结果。
3. As a 剪辑师, I want AI 建议包含动作（批准标准词/挂别名/丢弃）及推荐组或目标标准词与简短理由, so that 我能快速判断。
4. As a 剪辑师, I want 未确认前建议不修改标准词库、别名表与待审状态, so that 词表不被污染。
5. As a 剪辑师, I want 确认后走既有 resolve 路径写库, so that 与手工批准/挂别名/丢弃一致。
6. As a 剪辑师, I want 一次对多条待审跑批量 AI 分拣并看到建议清单, so that 不必逐条点 AI。
7. As a 剪辑师, I want 在清单中多选后点「应用选中」才写库, so that 错建议可跳过。
8. As a 剪辑师, I want 未选中的行即使清单里有建议也不写库, so that 批量不等于全写。
9. As a 剪辑师, I want 批量应用仍遵守「批准必须指定组」等既有规则, so that 不绕过半封闭。
10. As a 剪辑师, I want 批量分拣与单条建议使用同一任务路由槽（待审词 AI）, so that 设置简单。
11. As a 维护者, I want 可注入假 AI 测「未确认零写库 / 确认后写库」, so that CI 不依赖真实网络。

### 近义巡检真 AI

12. As a 剪辑师, I want 近义巡检默认走大模型生成合并建议清单, so that 能发现非包含关系的近义。
13. As a 剪辑师, I want 模型失败时降级为规则巡检并仍须确认合并, so that 离线也能清库。
14. As a 剪辑师, I want 合并须人工确认后执行（别名挂接 + 视频标签 bulk 归一到保留词）, so that 不静默改词表。
15. As a 剪辑师, I want 近义巡检仍为顶栏「更多」等次要入口, so that 不抢待审主路径。

### 标准词 AI 助手

16. As a 剪辑师, I want 在标签库对某个标准词请求 AI 助手, so that 不用整库巡检也能修一词。
17. As a 剪辑师, I want 助手建议可挂别名列表, so that 我能补全别名。
18. As a 剪辑师, I want 助手建议是否改到另一标签组, so that 分错组可纠。
19. As a 剪辑师, I want 分别确认别名与改组（或同一确认框内分项勾选）, so that 可只采纳一部分。
20. As a 剪辑师, I want 确认改组后走标签移动且标准词正文不变, so that 与拖拽改组语义一致。
21. As a 剪辑师, I want 助手不提供「并入另一标准词」, so that 合并规则只走近义巡检一处。
22. As a 剪辑师, I want 未确认时不写别名、不改组, so that 误点可撤销。

### 冷启动

23. As a 剪辑师, I want 词表冷启动本轮仍不调用大模型, so that 不会因分簇质量差污染草案。
24. As a 剪辑师, I want 冷启动仍可用规则分簇 + 可编辑草案 + 终审 commit, so that 第一版词表流程不断。

### 任务模型路由

25. As a 剪辑师, I want 在设置中为六个 AI 功能各自选择模型供应商与模型名, so that 性能与价格可按任务搭配。
26. As a 剪辑师, I want 六槽包含视频分类、标签生成、内容描述、待审词 AI、近义巡检、标准词 AI 助手, so that 分析与词表运营可拆开。
27. As a 剪辑师, I want 某功能未单独配置时回退到当前供应商, so that 不必六个槽全填才能用。
28. As a 剪辑师, I want 供应商档案仍只管理显示名/Key/URL（最多五个）, so that 连接与路由职责清晰。
29. As a 剪辑师, I want 保存设置后新请求按新路由解析, so that 不必重启。
30. As a 剪辑师, I want 进行中的分析本轮不因改路由而中断, so that 批次结果可预期。
31. As a 剪辑师, I want 从旧「档案内三模型 + 仅当前供应商」配置迁移后分析三槽仍可用, so that 升级无感。
32. As a 维护者, I want 路由解析（含回退）可单测, so that UI 不会串 Key 或串模型。

### Prompt 中心

33. As a 剪辑师, I want 在设置「Prompt 模板」中编辑分析全局 system_prompt, so that 角色头真正生效。
34. As a 剪辑师, I want 在同一页查看并编辑各标签组 local_prompt, so that 不必只在标签库里改才知道有什么。
35. As a 剪辑师, I want 编辑待审词 AI、近义巡检、标准词 AI 助手的 prompt 模板, so that 词表运营话术可调。
36. As a 剪辑师, I want 预览分析实际发出的 system + user（与运行时同一组装逻辑）, so that 我知道模型真正看到什么。
37. As a 剪辑师, I want 预览各标签库 AI 功能将使用的模板正文, so that 改完能核对。
38. As a 剪辑师, I want 冷启动 prompt 本轮可隐藏或不强调, so that 不暗示冷启动在走 AI。
39. As a 剪辑师, I want 旧版三份死配置 prompts.* 不再冒充可编辑生效项, so that 不被功能谎言误导。
40. As a 维护者, I want 运行时只从统一模板源读 prompt, so that 设置页与调用不再双轨。

### 标签芯片可读性

41. As a 剪辑师, I want 详情面板标签为浅底 + 组色左边条/边框且文字为主题正文色, so that 标签可读。
42. As a 剪辑师, I want 列表标签列与卡片上的标签芯片同一套样式, so that 三处扫读一致。
43. As a 剪辑师, I want 组色仍能区分氛围/主体/场景/动作/建议, so that 色条承担组身份。
44. As a 剪辑师, I want 疑似错别字用边框或图标提示而不是低对比红字盖在色块上, so that 错词可见且不牺牲可读。
45. As a 剪辑师, I want 悬停/选中态仍保持足够对比, so that 交互时字不消失。
46. As a 剪辑师, I want 标签库分栏列表不强制改成素材芯片样式, so that 管词界面不被无关改动打断。
47. As a 剪辑师, I want 浅色/深色主题下芯片都可读, so that 换主题不炸。

### 交叉与约束

48. As a 剪辑师, I want 所有标签库 AI 写库路径仍经人工确认, so that ADR-0004 成立。
49. As a 剪辑师, I want 界面与文档使用 CONTEXT 词汇, so that 概念不混。
50. As a 维护者, I want 标签库 AI 不直接绕过标签归一去改视频封闭组用词, so that 硬过滤不被旁路。

## Implementation Decisions

### 架构与 ADR

- 遵循 ADR-0004：AI 只建议；确认才写库；禁止模糊贴词。
- 遵循 ADR-0006：任务模型路由六槽；当前供应商 = 默认回退。
- 产品词一律 `CONTEXT.md`；冷启动 AI 保持停用（与既有产品开关一致）。
- 本特性**不**重做工作台/素材库导航、工作范围、操作目标集、分析目标集。

### 标签库 AI（核心）

**待审词 AI 建议（单条）**

- 输入：待审原文、来源组、当前标准词表摘要（可裁剪）、可编辑 prompt 模板。
- 输出：`approve_standard` | `link_alias` | `discard` + 必要参数 + 理由；可附 `source=model|rule`。
- 默认：按「待审词 AI」路由调模型；失败 → 既有规则建议。
- 确认后：`resolve_pending_tag` 等价路径；未确认零写库。

**批量待审 AI 分拣**

- 对当前待审队列（或可见子集）生成建议列表；UI 多选；「应用选中」逐条走与单条相同的确认写库路径。
- 默认不全选或仅预选高置信（若模型给置信；无置信则默认不预选或全不选——实现选「默认不预选」更安全）。
- 禁止一键静默全写；可提供「全选」但是用户手势，不是自动写库。

**近义巡检**

- 默认模型；失败规则降级；输出合并建议（keep + merge_as_aliases）；确认后别名 + bulk 替换视频标签 + 从标准词表移除被合并词（沿用既有 apply 语义）。

**标准词 AI 助手**

- 入口：标签库标准词上下文（右键或行内按钮）。
- 输出：建议别名列表、可选推荐 `group_id`（改组）；**无** merge-into-other-standard。
- 确认：别名 → add_synonym；改组 → 标签移动 API。
- 路由槽：标准词 AI 助手。

**冷启动**

- 不调用模型；不占路由槽；不在 Prompt 中心强调可编冷启动模板（可隐藏）。

### 任务模型路由

- 配置真相：每槽 `{ provider_id?, model_name? }`；缺省回退当前供应商 + 合理默认模型名。
- 六槽键名产品语义固定；分析三槽兼容旧档案 `models.video_classification|tag_generation|content_description` 迁移。
- 所有 AI 调用（分析子任务 + 标签库 AI）经统一「解析路由」再取 Key/URL/model；禁止旁路读死扁平字段而不经路由。
- 供应商上限五个不变。

### Prompt 中心

- 设置「Prompt 模板」页为完整清单：  
  - 分析：全局 system + 各组 local（折叠列表可编辑）+ 完整分析预览（既有 build/preview 逻辑）。  
  - 标签库 AI：三份可编辑模板 + 各功能预览（可用占位变量展示骨架）。  
- 运行时读取与设置写入同一存储源（tag_config 与/或 settings 中明确分区）；废除设置页再写死 `prompts.video_classification` 等三键为「生效 prompt」的叙事。
- 标签库既有 Prompt 配置入口可保留为捷径，但数据同源，避免双真相。

### 标签芯片样式

- 统一组件语义：浅底（随主题）、组色左边条或左侧强调边框、正文色 = 主题文字色、删除按钮用 muted。
- 组色映射沿用组身份（mood/subject/location/action/custom 等），色用于条/边而非整底实心填满。
- 应用面：详情 TagFlow、列表标签列绘制、卡片标签芯片；标签库分栏列表可不改。
- 错别字：边框/图标/tooltip；避免仅靠红字在色底上。
- 悬停：浅底略变，保持正文对比。

### 建议模块边界（无具体文件路径）

- **任务路由解析**（主接缝之一）：settings + task_key → credentials + model。
- **标签库 AI 门面**：单条待审建议、批量待审建议、近义巡检、标准词助手；均可注入客户端；写库仅 confirm API。
- **Prompt 模板存取**：按功能 get/set；分析预览复用组装。
- **标签芯片样式纯函数**：group_id/color → 样式 token（bg/border/text）。
- **UI**：设置路由表 + Prompt 中心；标签库批量待审对话框/面板；标准词助手对话框；芯片组件三处接线。

### 实现顺序建议（供 to-tickets，非强制）

1. 任务模型路由解析 + 迁移 + 设置 UI 六槽。  
2. 待审/近义真模型接线 + 注入测试。  
3. 批量待审 AI 分拣 UI 与应用选中。  
4. 标准词 AI 助手。  
5. Prompt 中心补齐 local + 标签库 AI 模板。  
6. 标签芯片浅底+色条三处统一。  
7. 人工验收清单。

## Testing Decisions

### 什么是好测试

- 只断言**外部可观察行为**：路由解析结果、未确认零写库、确认后库状态、芯片样式 token 对比关系、模板读写往返。
- 不断言 Qt 像素截图、真实网络调用次数。
- 标签库 AI 与分析 AI 一律可注入假实现。

### 测试接缝（Seams）— 请确认

| 优先级 | 接缝 | 锁定什么 |
|--------|------|----------|
| **主接缝 A** | **任务模型路由解析** | 六槽各自 provider+model；缺省回退当前供应商；迁移后分析三槽可用；禁止旁路 |
| **主接缝 B** | **标签库 AI 确认边界** | 单条/批量待审、近义、标准词助手：未确认零写库；确认后走 resolve/merge/move/synonym；可注入假 AI；冷启动不调模型 |
| **主接缝 C** | **标签芯片样式 token** | 浅底+组色条/边+主题正文色；错别字不靠低对比红底字；三处素材标签同源 token |
| **次接缝** | **Prompt 模板存取与预览** | 分析 system/local 与标签库 AI 模板可读写；预览与运行时同源键；旧 prompts.* 不作为生效源 |
| **非自动化默认** | 设置页表格操作手感、芯片在深浅主题下的像素观感、真实 API 批量待审 | 人工验收清单 |

**刻意不新增**平行写库入口：UI 只调上述门面；标准词助手不实现合并。

### 将测试的模块行为清单

- 路由：全缺省回退；单槽覆盖供应商；删供应商后槽回退；旧配置迁移。  
- 待审：假模型建议确认/取消；批量只应用选中。  
- 近义：假合并确认后别名与视频标签一致。  
- 标准词助手：确认别名；确认改组；拒绝合并动作。  
- 芯片：给定 group → token 满足「正文非随 bg 失控」。  
- Prompt：set 后 get 一致；分析预览含 local 变更。

### 既有测试 Prior art

- `tests/test_model_providers.py`、`test_tag_ai_assist.py`、`test_cold_start_ai.py`、`test_settings_persist.py`、`test_tag_normalize.py` 等服务层/纯函数风格；本特性沿用 pytest + 注入。

## Out of Scope

- 冷启动启用真 AI 分簇。  
- 导入草稿 AI 预审；素材侧库外词对齐清理。  
- 标准词助手「并入另一标准词」。  
- 删除磁盘视频文件。  
- 重新设计整站导航、工作范围、操作目标集。  
- 无限自定义路由槽或超过五个供应商。  
- 标签库分栏强制改成素材芯片风格。  
- 以真实在线大模型作为 CI 必过条件。  
- 卡片字段逐项显隐；整站配色重构（仅标签芯片可读性）。

## Further Notes

- 验收口诀：**真 AI 待审/近义** + **批量应用选中** + **标准词别名改组助手** + **六槽跨供应商路由** + **Prompt 全可见可改** + **浅底色条标签可读** + **确认才写库**。  
- 与 `analysis-library-ux-tag-ai`：消费其待审/巡检入口与确认边界，把规则默认升级为模型默认，并扩展批量与标准词助手。  
- 与 `providers-ops-target-tags-ux`：供应商档案保留；**推翻**「当前供应商驱动全部 AI」为 ADR-0006 路由。  
- 范围含核心词表 AI：建议 to-tickets 时 **② 相关票优先**，①③④ 可并行小票。

---

## Comments

### 2026-07-22 to-spec

> *Synthesized from grill-with-docs session; user invoked /to-spec after locking decisions (explicit shared-understanding phrase optional but decisions A/B locked per turn).*

- Status: `ready-for-agent`
- Locked: 待审/近义真模型；冷启动不走 AI；批量待审多选应用选中；标准词助手别名+改组；六槽任务路由；Prompt 全中心；标签浅底+组色条；素材三处统一
- ADR-0006 已写入
- Seams 已写入 Testing Decisions，请用户确认主接缝 A/B/C

### 2026-07-22 to-tickets

- 用户批准 8 票拆分并已发布至 `issues/01`–`08`
- Frontier（可立刻 `/implement`）：**01**、**07**（可并行）
- 核心路径：**01 → 02 → 04**
- 01 完成后可并行：**02**、**03**、**05**、**06**
- 最后：**08**（ready-for-human，依赖 01–07）

### 2026-07-22 implement

- **01–07** resolved（主会话实现/验收补齐）
- 要点：任务模型路由六槽 + client_for_task；待审/近义/标准词助手真模型+规则降级；批量待审多选应用；Prompt 全中心；浅底+组色条芯片（详情/列表/卡片）
- 测试：`test_model_providers` + `test_tag_ai_assist` 等通过；`test_work_scope` 中 scan 用例本地易挂，已排除核对
- **08** 仍为 ready-for-human（GUI 人工验收）
- 未 commit（待用户确认无 bug 后提交）