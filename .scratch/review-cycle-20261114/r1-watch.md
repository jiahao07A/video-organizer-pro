# R1-D 素材监控、内容身份与批次队列审查

Status: 第一轮完成，确认 1 项 P2 待主 Agent 裁决与修复
Baseline: dd609f8

## 已确认问题

### P2：超过最近 200 项的文件占用等待项无法由补扫恢复

- 位置：`core/media_watch.py` 中 `_scan_now()` 的 `retry_waiting` 查询及 `_register_video()` 的同类查询（本轮读取时约 352、462 行）；`core/media_registry.py:256` 的 `items()` 默认 `limit=200`、`ORDER BY i.id DESC`。并行修复可能移动行号，以符号和查询语句为准。
- 触发：任务累计至少 201 项，较旧项目在批次执行时文件暂不可读取，状态保持 queued/rename_ready/xmp_ready 且错误以 `FILE_WAIT_PREFIX` 开头，批次进入 paused。解除文件占用但不改文件大小和修改时间，等待周期补扫。
- 实际：监控将最近 200 项的界面分页结果当作任务完整集合。旧等待项未命中 `retry_waiting`，接着 stat 快速路径视为无变化而跳过，不再登记，`enqueue()` 无法把批次从 paused 唤醒；文件恢复可读后仍无限等待。
- 直接证据：`probe_watch_pagination.py` 构造 201 个真实合成文件和临时 SQLite，通过真实 `_process_video()` 登记，再模拟执行阶段写入的文件等待状态。`scan_now()` 和稳定检查后 `pending_count=0`、旧错误保留、`batch_state=paused`。仅在进程内把查询页扩大至 1000，再运行同一补扫，立即 `control_error=""`、`control_batch_state=queued`。
- 建议修复方向：后台业务查询不要调用分页展示 API；增加按 task_id 和规范化文件路径判断是否存在可恢复等待项的查询，供两处复用。回归应覆盖旧于最近 200 项的 queued、rename_ready/xmp_ready 等待项，保留不自动重跑已完成/人工决策项目的边界。

## 本轮验证

| 能力 | 证据 | 结果与边界 |
| --- | --- | --- |
| 配置继承、嵌套目录、过滤、离线目录与缺失恢复 | 现有 `test_media_watch.py` | 随模块套件通过 |
| 全库指纹、原记录与副本、并发登记、内容版本与人工修改保护 | 现有 `test_media_registry.py` | 随模块套件通过 |
| 批次持久恢复、手动优先、暂停取消、命名会话、XMP阶段重试、门禁与GUI选择快照 | 现有 `test_media_watch_workspace.py` | 随模块套件通过；AI为替身 |
| Windows 真实 watchdog 文件事件 | `probe_watch_events.py` | 真实 Observer 运行，新建中文文件入库、相同字节副本指向原编号、外部移动新建编号并标副本、旧路径完整补扫标缺失、路径重现恢复原编号、暂停不登记新增、恢复后登记、停止线程与Observer均退出 |
| 工作范围隔离 | 同上真实事件探针 | 事件处理结束工作范围仍为 `[]` |
| 旧等待项恢复 | `probe_watch_pagination.py` | 确认上述 P2；扩大查询集合的对照恢复成功 |

运行命令（均在 `pwsh.exe`）：

```powershell
rtk proxy python -X utf8 -m pytest tests/test_media_watch.py tests/test_media_registry.py tests/test_media_watch_workspace.py -q -rs
rtk proxy python -X utf8 .scratch/review-cycle-20261114/probe_watch_pagination.py
rtk proxy python -X utf8 .scratch/review-cycle-20261114/probe_watch_events.py
```

定向套件：**59 passed in 49.01s**，没有 skip。
真实事件探针：`observer_started=true`、`original_id=1`、`duplicate_of=1`、`external_move_new_id=3`、`old_missing_after_move=true`、`restored_original_id=1`、`restored_missing=false`、`pause_resume_registered=true`、`work_scope=[]`、`monitor_stopped=true`、`observer_stopped=true`。

## 范围及限制

已阅读 `media_watch.py`、`media_registry.py`、`watch_config.py`、监控工作区与高级配置、服务中门禁/指纹/监控路径联动、ADR-0013 和监控工作区规格。无产品源码或既有测试修改，无 commit，无真实视频、用户数据或凭据访问；探针使用独立系统临时目录且保留以避免目录清理操作。

本机为 Windows 11；不据此声称 Windows 10 实机验证。真实事件探针关闭 AI 和登录启动，未改真实注册表；登录自动启动、托盘可见交互、网络盘/移动磁盘热拔插与公网 AI 稳定性不在本代理证据范围。现有模拟占用测试不等同于所有 Windows 文件共享权限组合；本轮真实 watchdog 探针不能证明大目录长期内存稳定性。
