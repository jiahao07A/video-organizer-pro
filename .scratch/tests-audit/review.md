# 全量测试审查与清理报告

日期：2026-11-10。分支：`codex/tests-audit`。范围：全部55个测试模块、原有677个参数化场景及公共运行支持。两位subagent逐文件只读审查，主Agent复核并修改，原审查者第二轮独立复审。保留此前未提交的产品功能改动。

## Spec：测试是否验证需求

确认并修复3项：

1. **P1 默认设置跨测试污染**：`test_analysis_tagging_bugs.py`等使用浅拷贝，修改processing时同时修改全局默认值。已用临时服务复现，并将全部测试中的默认设置构造改为深拷贝。正序、逆序全量逐条监测均无默认值泄漏。
2. **P2 Windows路径测试未构造变体**：`test_analysis_targets.py`现在同时改变大小写和斜杠，确认原始输入不同，仅在Windows执行；复审内存变异移除normcase后，该测试会失败。
3. **P2 诊断分组测试只有一条素材**：`test_analysis_failure_center.py`改为3条素材、2类错误，验证组内数量1/2、首次仅2次请求及第二次缓存复用。

附带收紧GUI自动XMP关闭状态断言，移除列偏好重复宽松断言。第二轮该轴未发现确定剩余问题，独立定向33 passed。

## Standards：测试有效性与可维护性

确认并修复5项：

1. **P2 重命名测试自证**：`test_slim_analysis_fields.py`原先自行replace模板，现在调用实际batch_rename预览，检查文件仍保留及预期名称。
2. **P2 别名写入断言无效**：`test_tag_ai_assist.py`原先用非负计数作为兜底；现在验证数据库映射和精确新增数量。首审内存变异完全不写库也能通过原测试，证明覆盖缺口。
3. **P2 草稿别名断言恒真**：`test_tag_vocab.py`移除or True，分别验证草稿和提交后两条映射。
4. **P2 导出路径测试输入相同**：`test_post_review_hardening.py`现在用包含/./的等价路径，并断言字符串不同，验证ALE和FCPX过滤。
5. **P2 启动探针可能无限等待**：`test_runtime_performance.py`子进程增加30秒超时。

公共维护清理：辅助代码从测试模块移至tests/support的5个模块；9处重复Qt应用fixture统一到conftest；设置页fixture取消防抖、关闭并删除自身窗口；临时词库初始化失败不再吞掉；清理68处未使用导入；pytest.ini限定默认收集tests；新增tests/README.md说明入口、隔离规则、压力及环境跳过。没有删除测试文件或有效场景，没有全局屏蔽警告。第二轮该轴未发现确定剩余问题，独立定向44 passed。

## 验证证据

- 收集清单：原677个nodeid与修改后逐项一致，缺失0、新增0。
- 独立模块：55/55分别在全新Python进程通过；详情module-results.json。
- 正序全量：668 passed、9 skipped，默认设置泄漏0。
- 逆序全量：668 passed、9 skipped，默认设置泄漏0。
- 显式启用本地压力：9 passed，63.44秒；仅回环HTTP与合成素材。
- ruff限定检查F401/F811/F821通过；git diff --check通过。

正逆序监测通过verify_order.py临时pytest插件运行，各有25条既有Qt弃用警告和1条探针预导入anyio引发的断言重写提示；普通入口基线只有25条Qt警告。压力默认跳过是资源开销边界，本轮已单独实际执行；没有用skip掩盖失败。离屏测试不能替代真实桌面人工验收。本轮未修改真实数据、未调用真实外网API、未打包、未commit。用户确认完成且批准后再提交。
