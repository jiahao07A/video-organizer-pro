# 03 — 分析用词表子集与半封闭提示词

**What to build:** 每次 AI 分析只把精瘦的分析用词表子集（标准词、每封闭组约 15～25、合计约 80～100）与半封闭约束装进提示词；别名与待审词不进候选；大库自动裁剪而非失败。用户侧表现为模型选词清单短而稳，设置预览能看出约束。

**Blocked by:** 02 — 标准词·别名·标签归一与待审落库

**Status:** resolved

**Parent:** [spec.md](../spec.md)

## Acceptance criteria

- [x] 子集构建只含标准词；排除别名与待审词
- [x] 默认精瘦上限生效（每封闭组约 15～25、合计约 80～100）；超限按可解释规则裁剪
- [x] 空库/仅占位时可观测（便于提示先冷启动），不静默塞测试垃圾词当「正常库」而不告警
- [x] 分析提示词含各组「仅从下列标准词选择」或等价约束、max_count，以及子集列表
- [x] 建议组提示允许有限扩展并写明上限；封闭组 ai_expandable=false
- [x] 提示词/JSON 无 emotion；与 01 去情绪结论一致
- [x] 子集构建与提示词组装有自动化测试（可注入假词库）

## Comments

### 2026-07-20 to-tickets

- 用户批准 5 票发布；阻塞于 02

### 2026-07-20 implement

- `core/tag_vocab.py`：`build_analysis_vocab_subset`
- `build_analysis_prompts` 使用子集 + 半封闭文案 + 瘦库警告
- 测试：`tests/test_tag_vocab.py`