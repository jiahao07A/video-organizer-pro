# R2 测试有效性修复独立复审

Status: 通过；未发现需继续修复的问题
Reviewer: review_storage（没有编写本组测试修复）

## 审查范围与判断

检查 `tests/test_tag_vocab.py` 的实际diff、完整 `tests/test_review_test_effectiveness.py`、生产 `build_analysis_vocab_subset`/XMP入口及 `test_audit_probe.py` 注入实现，对照 `r1-tests.md`、`fix-tests.md`。本轮仅审查与执行，不改产品、测试或注入脚本。

- 别名/待审过滤：候选真正包含需要剔除的词；无排除、各单源排除和两者同时排除均有精确正反对照。合法词必须保留，不能用清空结果满足断言。
- 总预算：三个组共9词、各自只有3词，低于组上限5。预算0/1/5/8均会实际触发全局预算，精确期望各组分配；没有在测试中重写被测裁剪算法。
- XMP：真实服务、临时SQLite、三份不同内容合成文件；从产品 `sync_metadata_to_xmp` 入口输出两个真实侧车，并用独立标准XML解析器读取磁盘文件。断言全部 dc:subject 标签、摘要原文及语言属性、层级路径、分类前缀与完整输出文件集合；未选择第三份素材提供负对照。特殊字符经过解析后的值与原始输入比较，测试不直接调用产品内部转义器构建期望。
- 原有断言未降级，没有新增skip条件或产品修改。Windows/剪辑软件兼容性仍不能从本组XML测试推断。

## 独立运行证据

1. `rtk proxy python -X utf8 -m pytest tests/test_tag_vocab.py tests/test_slim_analysis_fields.py tests/test_review_test_effectiveness.py -q -rs`：**29 passed in 10.25s**，exit=0。
2. `rtk proxy python -X utf8 -m ruff check tests/test_tag_vocab.py tests/test_review_test_effectiveness.py --select F401,F811,F821`：All checks passed，exit=0。
3. `rtk proxy python -X utf8 .scratch/review-cycle-20261114/test_audit_probe.py aliases`：**4 failed、22 passed in 8.64s**，exit=1。业务断言 `test_tag_vocab.py:130/153` 清楚显示男人/别名/待审词被错误保留。
4. 同上 `budget`：**4 failed、22 passed in 8.66s**，exit=1。`test_tag_vocab.py:177` 显示预算0/1/5/8时错误返回全部9词。
5. 同上 `xmp`：**3 failed、18 passed、1009 deselected in 30.39s**，exit=1。全部失败位于 `test_review_test_effectiveness.py:61`，实际标签列表只有 `室内&大厅` 而期望三词；产品仍报告两侧车写入成功，证明断言检测的是真实持久内容丢失。

三个变异均仅替换当前Python进程里的函数实现，源码未改写；每项都有 MUTATION_WITNESS 及明确业务断言失败，无导入失败或注入失败。anyio预导入提示属于探针调用pytest的重写警告，不是失败原因。并行新增测试导致deselected数量变化，不影响三项具体失败证据。

## 边界

这轮证明T1/T2/T3三种已知漏检现在会变红，并不证明所有现有测试对任意回归均有效。XMP全部字段结构通过仍需真实剪辑软件导入验收。无用户数据、真实素材、公网AI访问；无commit和目录删除。
