# 02 — 标准词·别名·标签归一与待审落库

**What to build:** 建立半封闭「执法」主路径：库内标准词 + 别名映射；落库前标签归一（别名→标准词；封闭组库外词丢弃并记入待审词；建议组库外词可挂视频但不自动进库；禁止模糊近邻贴词）；分析/批量改标签等写入路径走归一。用户侧表现为打标更干净、词表不因模型乱造词而膨胀。

**Blocked by:** None — can start immediately（可与 01 并行）

**Status:** resolved

**Parent:** [spec.md](../spec.md) · ADR-0004

## Acceptance criteria

- [x] 标准词与别名可持久化查询；同一含义仅标准词写入视频与导出主关键字
- [x] 标签归一纯逻辑可单测：别名映射、封闭组丢弃+待审增量、建议组可保留库外词、max_count 截断
- [x] 明确无「模糊贴最近标准词」行为，并有回归测试锁死
- [x] 分析（及批量替换等）写入视频前调用归一；落库 tags 不含封闭组库外原文
- [x] 待审词可持久化（至少 pending 状态与原文/组/时间等最小字段）；待审词不进入分析候选
- [x] 建议组新词默认不自动写入标签库标准词表

## Comments

### 2026-07-20 to-tickets

- 用户批准 5 票发布；主接缝票；可与 01 并行
- 筛选「别名扩展命中」逻辑优先在本票测通；控件接线可留 05

### 2026-07-20 implement

- Status: claimed → resolved
- 新增 `core/tag_normalize.py`（纯逻辑主接缝）
- `AIHandler.apply_tag_normalization` + `analyze_video` 去掉模糊匹配与建议组自动 `add_tag`
- `pending_tags` 表与 DB API；`bulk_replace_tags` 经别名归一
- 筛选 `alias_map` + `expand_tags_for_filter`（UI 接线留 05）
- 测试：`tests/test_tag_normalize.py`；全量 `tests/` 34 passed