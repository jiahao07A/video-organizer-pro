# 批量登记独立复审

状态：发现的 1 项内容身份稳定性问题已由登记代理修复并经独立复审通过；本轮重点路径未发现剩余阻断问题。

## 发现：单条登记等待数据库锁时可以把旧指纹标为已验证

优先级：P1。位置：`core/media_registry.py` 的 `register`，调用 `_prepare_fingerprint` 后进入 `db.lock` / `BEGIN IMMEDIATE`，未像 `register_many` 一样在事务内重新 stat。

场景：监控单条登记已完整读取文件；批量扫描或另一个数据库操作占锁；文件在等待锁期间继续写入；锁释放后单条登记仍写入旧 hash、旧 size/mtime，设置 `fingerprint_state=verified`。后续去重门禁可能据此采用错误内容身份。

隔离复现：使用 `tempfile.mkdtemp(prefix='scan-registry-review-')` 创建新 SQLite 和 6 字节合成文件；主线程持有 `db.lock`，后台调用 `register(index_existing=False)`；包装 `_prepare_fingerprint` 在原调用完成后设置 Event；主线程等待 Event 后将文件改为 33 字节，再释放锁。结果：`errors=[]`、`verified='verified'`、`stale_hash=True`、`stored_size=6`、`actual_size=33`。不接触真实库和素材。

建议：单条登记取得事务锁后，执行与批量一致的 size/mtime 重查；变化则抛错、事务回滚，不改分析结果、人工编辑、内容版本或重复分组。补确定性锁等待回归。此问题在原代码也可存在，但本轮对单条/批量共享核心做了修改，并承诺文件变化拒绝，应一起覆盖。

## 其它复审结论

- 批量完整字节哈希在有界池中执行；结果按输入顺序入库，不以线程完成先后争夺本批编号。尾字节测试能排除抽样指纹。
- 64 项默认事务和逐项保存点覆盖视频记录、旧内容快照、内容组和发现事件；登记成功只在事务提交后累计，保存点回滚后重建路径索引，避免残留未提交编号。
- Windows 等价路径索引先按规范路径缩小候选，再 samefile 核实；复用连接的 data_version 能检测其它连接提交并刷新索引；新行在当前批索引同步。不同路径同内容仍各有编号。
- 未变化的重新扫描保留既有成果；稳定内容替换使用既有版本历史及人工冲突 review 规则，未为优化引入新的清空行为。
- `index_existing` 对 pending 历史按编号补指纹，旧记录先于新批分类；force 可重试离线文件，不因 `_indexed` 永久略过。
- 测试使用真实 SQLite 触发器在保存点中途失败，验证不是仅 mock 断言。并发单条测试能验证原记录归属，但没有覆盖上面的锁等待后文件变化。
- 尚缺外层 COMMIT 失败的注入测试；实现让该异常上抛、未把该批加入成功结果，未发现错误吞没。已提交的早期批次会保留，属于分批事务语义。

## 执行验证

`rtk proxy python -X utf8 -m pytest tests/test_scan_registry_batch.py -q -W error::pytest.PytestUnhandledThreadExceptionWarning`

结果：13 passed in 1.55s（Windows 等价路径项实际执行）。未进行重型性能测量。

## 修复后二次复审

登记代理在单条 `register` 的 `BEGIN IMMEDIATE` 后增加 `_verify_fingerprint_snapshot`，与批量共用校验逻辑。新增 `test_single_register_rechecks_after_database_lock_wait_and_preserves_prior_state`，用真实数据库锁、真实文件替换及 Event 确定性安排时序，要求抛出 OSError；并验证人工摘要、状态、指纹、版本、分组、历史及发现计数不变。

独立重新阅读实现和新增测试，并重新执行上述定向命令：14 passed in 1.56s。该发现关闭。

## 双批有界预取的最终独立复审

已只读复审 `register_many` 最新 `deque` 调度与新增两项真实并发测试：未发现剩余阻断项。

- 初始化只提交当前批与下一批；每轮先弹出并消费当前批，再完成本批事务、进度、`on_batch`，最后只补一个新批。当前局部 future 加队列最多两个批次，线程池实际线程上限仍为 4，没有把全目录的 future 一次提交。
- 下一批哈希可以与本批提交/缩略图调度重叠，但读取到的指纹仍需经过本批事务中的 stat 校验。新增 Event 回归在上一批已提交回调里改变已经完成预取的下一文件，验证下一批以“校验后变化”错误拒绝入库。
- `_register_on_connection` 仍按批序和输入路径序调用，后续批次不能提前争夺本轮编号；最终结果仍在全部批次后从 SQLite 读取，保留最终重复分类。
- `on_batch` 仍位于事务提交、数据库锁释放后。不可恢复的 future、进度或消费回调异常上抛；线程池上下文会等待至多两个批次内已提交的只读任务结束，不会继续提交剩余目录，已提交批次保持分批事务语义。
- `ObservedExecutor` 使用真实 ThreadPoolExecutor 只记录 submit；Barrier(4) 证明确实达到 4 个实际并发线程，Event 阻塞第一批消费回调时断言第三/四批未提交、未启动，能抓取无限预取/仅串行假并发的错误。

最终定向验证：`rtk proxy python -X utf8 -m pytest tests/test_scan_registry_batch.py -q -W error::pytest.PytestUnhandledThreadExceptionWarning` → 19 passed in 2.24s。未运行大型性能计时。

## Qt offscreen 首次扫描到首屏就绪的可执行建议

调用链：`background_scan_work_scope` 的扫描 worker 完成后，`on_ok` 调用真实 `load_data`；它直接启动 `_load_query_page(reset=True)`，后台 SQL 完成才 `model.update_data(page.rows)`、更新 `_page_total/_page_offset`。因此不能只等 `_io_worker is None` 就计结束；必须同时等目录 worker/页状态与模型行数。

在现有 benchmark 的隔离数据、日志、设置和只读 source 初始化后，用下面代码替换同步扫描计时部分即可。不得 monkeypatch `load_data`、SQL、哈希或缩略图。`QApplication` 必须在 WorkstationView 前创建；环境变量必须在首次导入 Qt 前设置。为避免额外影响主 Agent 正在进行的计时，此代码仅作建议，未执行 1455 端到端性能测量。

```python
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from gui.views.workstation import WorkstationView
from core.manual_test_environment import wait_for_background_work

app = QApplication.instance() or QApplication([])
# service 必须使用 benchmark 新建的数据目录；此时工作范围为空。
service.replace_work_scope([], scan=False)
view = WorkstationView(service)
assert wait_for_background_work(view)
app.processEvents()
service.replace_work_scope([str(source.resolve())], scan=False)
events, heartbeats = [], []
view.status_message.connect(lambda text: events.append((time.perf_counter(), text)))
timer = QTimer(view)
timer.setInterval(20)
timer.timeout.connect(lambda: heartbeats.append(time.perf_counter()))
timer.start()
expected = 1455
started = time.perf_counter()
view.background_scan_work_scope(reason='端到端计时')
deadline = started + 120
while time.perf_counter() < deadline:
    app.processEvents()
    ready = (
        getattr(view, '_io_worker', None) is None
        and getattr(view, '_catalog_worker', None) is None
        and not view._page_loading and not view._query_pending
        and view._page_total == expected
        and view.model.rowCount() == min(expected, view._page_limit)
        and view._page_offset == min(expected, view._page_limit)
    )
    if ready:
        break
    time.sleep(0.001)
elapsed_to_first_page = time.perf_counter() - started
timer.stop()
assert ready, view.status_label.text()
assert service.db.execute_query('SELECT COUNT(*) n FROM videos')[0]['n'] == expected
assert view.analyze_btn.isEnabled()
assert view.progress_bar.isHidden() and view.sub_progress_bar.isHidden()
assert events and heartbeats
print({'first_page_seconds': elapsed_to_first_page,
       'max_ui_heartbeat_gap': max(b-a for a, b in zip(
           [started] + heartbeats, heartbeats + [time.perf_counter()]))})
assert wait_for_background_work(view)
view.close()
view.deleteLater()
app.processEvents()
```

此验证覆盖真实后台扫描、Qt 消息交付、目录 SQL 和模型首屏就绪。offscreen 不证明 Windows 可见窗口的像素布局或真实视频播放器表现；本轮用户要求暂不桌面自动化。
