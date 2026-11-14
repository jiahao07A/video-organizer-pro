# 监控等待项分页恢复修复

Status: 修复及定向回归通过；待主 Agent 与原审查者复审

## 修改范围与行为

- `core/media_registry.py` 新增 `has_waiting_file(task_id, path)`：按任务、规范化精确路径、`FILE_WAIT_PREFIX` 错误前缀和 queued/pending/rename_ready/xmp_ready 四个可恢复阶段查 `SELECT 1 ... LIMIT 1`。不加载全部记录，不使用分页结果判断业务状态。
- SQLite 内置 lower 不支持 Unicode 大小写，查询连接局部注册 `media_watch_path_key` 复用现有 `path_key`，确保 Windows 的 Unicode 大小写和斜杠变体仍保持一致。没有新增依赖、表、索引或数据迁移。
- `core/media_watch.py` 的 `_scan_now()` 与 `_register_video()` 统一调用精确查询。`items()` 的默认200条及UI分页不变；已有登记、重复身份、批次暂停唤醒与人工决策边界不变。
- 新增 `tests/test_review_watch_recovery.py`，6项测试。每个分页恢复场景真实创建202个不同内容的合成文件，通过真实服务、SQLite、监控登记形成202项；断言目标不在最近200项，分别覆盖四种恢复阶段、周期扫描和基线历史跳过分支。额外验证近期终态不唤醒、错误前缀/阶段/任务/路径隔离和Windows Unicode路径等价。

## 验证

- `rtk proxy python -X utf8 -m pytest tests/test_review_watch_recovery.py -q -rs`：**6 passed in 49.20s**。
- `rtk proxy python -X utf8 -m pytest tests/test_media_watch.py tests/test_media_registry.py tests/test_media_watch_workspace.py -q -rs`：**59 passed in 43.29s**。
- `rtk proxy python -X utf8 .scratch/review-cycle-20261114/probe_watch_pagination.py`：旧于200项的原始复现现在直接 `error=""`、`batch_state="queued"`；扩大展示页的对照与其相同，不再需要扩大分页。
- `rtk git diff --check`：通过。

## 风险与限制

SQL查询返回至多一条且利用现有 stage/task 队列索引限制候选；路径函数只在连接局部存在，未改变存储格式。大量同任务等待项下仍需扫描候选错误和规范化路径，本轮不承诺长时间性能。测试未启动外部AI、Observer线程或修改注册表；文件不可读状态通过真实DB设置既有等待标记，OS层共享占用组合未新增验证。本机Windows11；Windows10实机仍未验证。

仅修改授权的两个core文件及一个新测试文件，未修改其他产品/测试，未commit，未删除目录。其他代理的并行修改不属于本执行包。
