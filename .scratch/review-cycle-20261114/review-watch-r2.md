# 监控等待恢复修复独立二次审查

Status: R2 完成；未发现本修复中的新增可确认问题
Reviewer: R1-D 原问题发现者；未参与该监控修复实现

## 代码与范围复核

- 核对 `core/media_registry.py::has_waiting_file()`、`core/media_watch.py::_scan_now()`/`_register_video()` 实际 diff，以及 `tests/test_review_watch_recovery.py`。
- 两处后台恢复判断均已脱离界面 `items()` 分页，直接按 task_id、规范化完整路径、四个可恢复阶段及字面错误前缀查询存在性。
- SQL 的 `substr()` 使用参数绑定和精确比较，文件路径通过同一 `path_key` 规则处理 Windows Unicode 大小写、斜杠和绝对路径。连接内函数注册在数据库锁内；现有队列索引可限制阶段/任务候选。没有新增表或改变存储数据。
- `items()` 默认200项、排序与offset未修改。UI继续分页；后台不加载并返回全部记录。
- 新测试真实建立202个不同内容文件、真实SQLite队列，断言目标已落在最近200项之外。覆盖四种恢复阶段、补扫和基线跳过两条入口，并检查相同前缀的终态、其他任务、其他路径、其他错误不被误唤醒。

## 独立重跑证据

```powershell
rtk proxy python -X utf8 -m pytest tests/test_review_watch_recovery.py tests/test_media_watch.py tests/test_media_registry.py tests/test_media_watch_workspace.py -q -rs
```

**65 passed in 64.86s**，没有失败或跳过。包括新增6项及既有59项。

```powershell
rtk proxy python -X utf8 .scratch/review-cycle-20261114/probe_watch_pagination.py
```

最初由本审查者编写、在未修复代码中已复现失败的201文件探针，现在直接得到：

```json
{"case":"older_than_200","pending_count":0,"error":"","batch_state":"queued","control_error":"","control_batch_state":"queued"}
```

修复后不扩大分页就已清除旧错误并恢复 queued；扩大展示页的对照不再产生不同结果。

```powershell
rtk proxy python -X utf8 .scratch/review-cycle-20261114/probe_watch_events.py
```

真实 Windows 11 watchdog Observer 再次确认：中文新文件入库、内容副本、外部移动新编号、完整补扫缺失、旧路径重现恢复原编号、暂停不处理新素材、恢复登记、工作范围仍为空、停止后监控和Observer线程退出。输出与R1正常事件探针一致。

## 结论与边界

R1 已确认的旧等待项分页问题在原始复现及新增回归中均已消失，未发现本次修复的可确认回归。此结果仅覆盖当前Windows11、合成文件和临时数据库；未宣称Windows10实机、网络共享、真实磁盘长期负载或所有文件共享权限组合已验证。没有修改产品、测试或提交；仅新增本报告。
