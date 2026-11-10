# 第二轮全量测试深入审查

日期：2026-11-10。审查当前工作区全部55个测试模块、共享fixture和support及运行配置；两名审查者分别复查26和29模块，主Agent复核以下全部7项。仅审查，不修改产品和测试源码，不提交。

## 已确认发现

全部为P2测试覆盖缺口，不是已确认的当前产品故障。每项均在独立Python进程临时替换实现，退出后自动恢复；仅使用合成输入和临时数据。

|定位|漏检回归与证据|建议|
|---|---|---|
|tests/test_real_workflow_regressions.py:16–19|1400条范围但数据库为空，只验证空结果。将超过1000条的范围查询强制返回空，相关4模块仍34 passed。|实际入库超过1000条，加范围外对照，断言返回完整目标且排除范围外素材。|
|tests/test_analysis_failure_center.py:20–32|仅有一条匹配记录，无非匹配对照。完全忽略category条件后，失败中心、真实工作流和恢复控件3模块仍79 passed。|构建不同原因、阶段与状态记录，验证列表、计数、paths_only及预览目标一致。|
|tests/test_tag_config_groups.py:36–39|磁盘断言使用版本正确或原词存在。保留正常内存但把磁盘写成version=6.0且tag_groups为空，目标测试仍1 passed。|分别验证版本和全部预期词组，再创建服务检查重启读取。|
|tests/test_settings_catalog.py:172–187|服务已绑定tags.json，测试之后修改模块常量并检查tag_config.json，遗漏真实写入。将未保存预览文本写到服务真实配置文件后仍1 passed。|读取service._tag_config_path，精确比较预览前后磁盘和内存；移除晚修改常量和可选exists断言。|
|tests/test_analysis_failure_center.py:42–63|诊断mock只计调用，不检查请求内容。移除发送前脱敏，整个模块仍40 passed。合成捕获证明变异会把模拟密钥、完整路径和data:image发给AI门面；当前基线正确脱敏。|捕获实际请求参数，断言敏感内容缺失且阶段、状态码等必要上下文保留。|
|tests/test_frame_color_bgr.py:44–55|测试自行组合resize和encode，绕过extract_frames。真实抽帧入口恢复错误BGR→RGB转换，颜色及素材显示模块仍14 passed。|用合成红色Capture调用真实extract_frames，解码实际返回帧检查通道。|
|tests/test_analysis_job_metrics.py:50–60|持久化只查返回路径后缀和索引。丢弃全部job_*.json主文件写入，模块仍5 passed。|读取返回路径的JSON，验证任务ID与真实计数，检查索引追加保留既有任务。|

## 复现证据

- deep_probe.py：large_scope、category。主Agent运行结果34及79 passed；探针预导入触发1条anyio重写提示。
- disk_probe.py：groups、preview。主Agent分别独立复现1 passed。
- spec_deep_probe.py：metrics、bgr、diagnostic。主Agent独立复现5、14、40 passed。
- spec_diagnostic_evidence.py及JSON：主Agent独立捕获，baseline_key_leaked=false，mutated_key_leaked=true；没有真实网络请求。
- spec-deep-review.md：第一组26模块覆盖清单与详细说明。

在仓库根目录使用pwsh.exe执行rtk proxy python -X utf8 .scratch/tests-audit/对应探针.py 模式；deep_probe.py需通过runpy运行以保留仓库导入路径，命令为：rtk proxy python -X utf8 -c "import runpy,sys; sys.argv=['deep_probe','category']; runpy.run_path('.scratch/tests-audit/deep_probe.py',run_name='__main__')"。large_scope同样替换模式。

## 限制与工作区

本轮聚焦断言有效性与回归捕获，不重复上一轮全量和压力测试，不把变异运行的通过数相加当成唯一测试数量。上一轮的668 passed、9 skipped是之前的基线，不是本轮重新全量验证结果。没有修改tests/core/gui，没有访问用户素材、数据库或外网API，没有打包、commit或PR。原工作区未提交内容均保留。当前基线脱敏和颜色路径正常，其他变异仅证明相应测试的检测缺口。
