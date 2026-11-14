# API-01 诊断请求内容契约修复

Status: 修复和定向验证完成，等待独立复审

## 修改

- `core/video_organizer_service.py::diagnose_analysis_failures()` 只将内容数组中的 JSON 字符串改为 `{"type":"text","text":...}` 对象；未改变分组、脱敏、缓存、重试预算或诊断结果处理。
- `tests/test_analysis_failure_center.py` 中既有出站脱敏捕获按标准 `content[0]["text"]` 读取，其余敏感内容缺失与技术上下文断言全部保留。
- 新增 `tests/test_review_diagnosis_contract.py`：真实临时 SQLite 素材和 `RecoveryStore.finish_failure` 记录 → 实际诊断入口 → 实际网关/SDK → 回环 HTTP 严格校验数组内容的 type/text；验证诊断成功和数据库缓存，再次执行仅复用缓存而不追加请求。
- 新测试检查实际出站载荷仍有类别、阶段、503 状态、provider/model、超时消息和 HTTP 依据；模拟密钥、完整素材路径、image data 与 base64 均不外传。

## 验证

```powershell
rtk proxy python -X utf8 -m pytest tests/test_review_diagnosis_contract.py tests/test_analysis_failure_center.py -q -rs
```

最终结果：42 passed in 22.12s。`rtk git diff --check` 通过。
首次执行新测试误把包含 timeout 的错误预期为 service，实际规范分类 network；已修正测试预期后重跑全部指定模块通过，未改变产品分类行为。

## 文件所有权与限制

服务文件单行改动完成后已立即通知主 Agent 释放编辑权，后续没有再编辑该文件；未修改 GUI。未访问公网AI、没有真实凭据、未安装依赖、未提交或删除目录。回环测试证明标准 SDK 契约与持久缓存可用，不能证明所有上游供应商均可用。
