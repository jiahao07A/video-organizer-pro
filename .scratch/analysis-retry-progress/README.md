# analysis-retry-progress

Spec: [spec.md](./spec.md)  
Status: **implemented**（待 GUI 验收后 commit）  
Created: 2026-07-22  
ADR: [0005](../../docs/adr/0005-analysis-retry-and-progress.md)

## Tickets

| ID | 标题 | Status |
| --- | --- | --- |
| 01 | 策略纯逻辑 | **resolved** |
| 02 | 调用层+单条层 | **resolved** |
| 03 | 批次补跑+取消 | **resolved** |
| 04 | 双进度+行态+取消 | **resolved** |
| 05 | 设置+人测清单 | **resolved** |

## 测试

`pytest tests/test_analysis_job_policy.py tests/test_analysis_orchestration_retry.py tests/test_analysis_flow_service.py tests/test_analysis_tagging_bugs.py tests/test_analysis_targets.py` → **41 passed**

## 人测

见 [issues/05-settings-and-human-acceptance.md](./issues/05-settings-and-human-acceptance.md)