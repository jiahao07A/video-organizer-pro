# workbench-library-split

Status: implemented（待用户 GUI 验收后 commit）

| 文档 | 说明 |
|------|------|
| [spec.md](./spec.md) | 完整规格 |
| ADR-0001 | 工作台 / 素材库 / 工作范围 |
| ADR-0002 | 分析结果字段瘦身 |

## Tickets

| ID | 标题 | Status |
|----|------|--------|
| [01](./issues/01-slim-analysis-fields.md) | 分析结果字段瘦身 | **resolved**（实现完成） |
| [02](./issues/02-work-scope-core.md) | 工作范围核心 | **resolved**（subagent） |
| [03](./issues/03-operation-scope-bounds.md) | 操作边界 ⊆ 范围 | **resolved** |
| [04](./issues/04-workbench-accumulate-paths.md) | 工作台累加路径 | **resolved** |
| [05](./issues/05-material-library-page.md) | 素材库页 + 勾选累加 | **resolved** |
| [06](./issues/06-advanced-filter-both-pages.md) | 高级筛选扩展（两页） | **resolved** |
| [07](./issues/07-ui-preferences-remember-scope.md) | 界面偏好 + 记住范围 | **resolved** |

## 测试

```text
pytest tests/test_ops_filter_scope.py tests/test_slim_analysis_fields.py tests/test_work_scope.py -q
→ 21 passed
```

## 验收后

确认无 bug 后回复「可以 commit」。