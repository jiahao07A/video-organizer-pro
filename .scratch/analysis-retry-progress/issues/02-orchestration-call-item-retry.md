# 02 — 服务编排：调用层 + 单条层重试

**Status:** resolved  
**Blocked by:** 01  
**Parent:** [spec.md](../spec.md)

## Acceptance criteria

- [x] `_get_api_response` 调用层重试
- [x] `_process_single_video` 单条层重试
- [x] XMP 失败不推翻成功
- [x] 集成测 `test_analysis_orchestration_retry.py`

## Comments

### 2026-07-22 implement

- 接入 AIHandler 取消事件与可注入 sleeper
- 编排测通过