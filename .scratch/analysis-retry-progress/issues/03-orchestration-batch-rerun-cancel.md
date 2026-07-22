# 03 — 服务编排：批次补跑 + 协作式取消

**Status:** resolved  
**Blocked by:** 02  
**Parent:** [spec.md](../spec.md)

## Acceptance criteria

- [x] 自动批次补跑 1 轮
- [x] 可关补跑
- [x] `request_cancel_analysis` 协作式取消，取消后不 C
- [x] 轮次事件/快照 first|rerun

## Comments

### 2026-07-22 implement

- `run_analysis` 双轮 + cancel Event
- 测试覆盖补跑/关闭/取消/XMP