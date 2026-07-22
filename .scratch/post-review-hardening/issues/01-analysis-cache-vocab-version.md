# 01 — 分析缓存：词表变更后不再静默用旧标签

**What to build:** 剪辑师修改标准词/别名/分析用词表相关配置后，再对同一文件做**非强制**分析时，不得静默复用会带出过期标签的旧 AI 缓存结果。缓存键须纳入词表（或提示/归一）版本信息；若命中历史缓存，仍须按**当前**标签归一规则处理，或等价地使缓存失效。强制重新分析仍可跳过读缓存（既有语义保留）。

**Blocked by:** None — can start immediately

**Status:** resolved

**Parent:** [spec.md](../spec.md)

## Acceptance criteria

- [x] 词表/别名变更后，非强制分析不会贴出仅来自旧缓存、未按新词表归一的标签结果
- [x] 缓存键或失效策略可解释，且有自动化测试覆盖「词表变 → 不脏用」
- [x] 强制重新分析仍不读旧缓存（或等价跳过读缓存）行为保持
- [x] 不扩大本票范围到导出/扫盘/UI 大改

## Comments

### 2026-07-22 to-tickets

- 用户批准 10 票线性发布；本票为 frontier（第 1 刀正确性）

### 2026-07-22 implement

- `build_vocab_cache_fingerprint` + cache_key 含词表指纹；命中后再归一
- `tests/test_post_review_hardening.py` 覆盖