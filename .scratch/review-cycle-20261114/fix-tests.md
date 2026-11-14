# 测试有效性缺口修复

Status: 实现和故障注入验证完成，待主 Agent 独立复审

## 修改范围与行为

- `tests/test_tag_vocab.py`：原候选列表真实放入男人（别名）与赛博（待审），精确核对各组及总数，保留占位剔除、组内上限和使用次数优先断言；新增4组参数覆盖不排除、只排除别名、只排除待审、两者同时排除的正反对照。
- `tests/test_tag_vocab.py`：新增4组总预算0/1/5/8。每组各3个词，小于组上限5，合计9词明确超过总预算，精确核对每组剩余分配及总数；不再用从未触发的20词上限证明总预算有效。
- 新增 `tests/test_review_test_effectiveness.py`：真实临时SQLite、3条不同合成素材，仅选择2条输出，精确断言实际sidecar集合。解析全部生成XMP的 `dc:subject` 全标签、`dc:description` 原摘要和语言属性、`lr:hierarchicalSubject` 父子关系/配置标签组兜底/分类前缀。3组参数分别覆盖启用/禁用层级与分类前缀，标签和摘要含 `&`、`<` 以验证XML往返。
- 没有修改产品文件、其他既有测试、skip条件；FCPX测试交由导出修复负责人，不重复变更。

## 验证

```powershell
rtk proxy python -X utf8 -m pytest tests/test_tag_vocab.py tests/test_slim_analysis_fields.py tests/test_review_test_effectiveness.py -q -rs
# 29 passed in 9.45s
rtk proxy python -X utf8 -m ruff check tests/test_tag_vocab.py tests/test_review_test_effectiveness.py --select F401,F811,F821
# All checks passed
rtk git diff --check
# 通过
```

随后分别运行首轮同一组进程内故障注入，生产文件始终不改写，进程退出即恢复。以下非零退出全部由新业务断言产生，未发生导入或探针异常：

| 命令末尾模式 | 人为错误 | 首轮修复前 | 修复后 | 失败证据 |
|---|---|---|---|---|
| `test_audit_probe.py aliases` | 完全移除别名/待审排除分支 | 18 passed | 4 failed、22 passed | `test_tag_vocab.py:130/153` 实际集合包含别名/待审 |
| `test_audit_probe.py budget` | 忽略total_max | 18 passed | 4 failed、22 passed | `test_tag_vocab.py:177` 实际9词超过0/1/5/8预算 |
| `test_audit_probe.py xmp` | `dc:subject` 只输出第一标签 | 14 passed | 3 failed、18 passed、927 deselected | `test_review_test_effectiveness.py:61` 实际1标签而期望3 |

完整命令前缀均为 `rtk proxy python -X utf8 .scratch/review-cycle-20261114/`。XMP筛选统计包含其他代理同时新增测试，故与首轮deselected数不同；只将本次3个明确业务断言失败作为证据。预先导入后调用pytest有1条anyio断言重写提示，不影响故障注入结论。

## 风险、限制与下一步

这些测试证明三种已确认漏检回归现在能够检测；不能代表所有可能故障均已进行变异测试。XMP结构与业务字段验证不能代替真实Premiere/Resolve软件导入验收。等待主 Agent 检查差异、独立复审和完整集成回归；未commit、未删除目录、未触碰用户素材及配置。
