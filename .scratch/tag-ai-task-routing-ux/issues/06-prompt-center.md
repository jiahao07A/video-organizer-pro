# 06 — Prompt 全中心

**What to build:** 设置「Prompt 模板」成为**全部可编辑 Prompt 中心**：分析全局 system、各标签组 local_prompt、待审词 AI / 近义巡检 / 标准词 AI 助手模板均可查看与修改；提供与运行时同源的预览（分析完整拼装；标签库 AI 按功能模板预览）。冷启动模板本轮隐藏。旧 `prompts.*` 三键不再冒充生效源。运行时只从统一模板源读取。

**Blocked by:** 01 — 任务模型路由

**Status:** resolved

**Parent:** [spec.md](../spec.md) · ADR-0006

## Acceptance criteria

- [x] 设置页可编辑分析全局 system_prompt 且分析链路真正使用
- [x] 设置页可查看并编辑各标签组 local_prompt；保存后分析预览/调用同源
- [x] 可编辑待审 / 近义 / 标准词助手三份模板；对应 AI 调用读取同一源
- [x] 分析完整 system+user 预览可用；标签库 AI 模板可预览骨架
- [x] 冷启动 prompt 不作为本页主推可编辑项（隐藏或不强调）
- [x] 旧 prompts.* 死配置不再呈现为「改了就生效」；模板读写可测

## Comments

### 2026-07-22 to-tickets

- 用户批准发布；次接缝 Prompt

### 2026-07-22 implement

- Status: resolved
- 设置 Prompt 页：system + 各组 local + 三份 tag_ai_prompts + 统一预览；保存走 set_system_prompt / set_group_local_prompt / set_tag_ai_prompt