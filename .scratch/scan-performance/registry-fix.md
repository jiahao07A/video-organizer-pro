# registry 批量登记修复与独立复审

## 实施范围

仅修改 `core/media_registry.py`，新增 `tests/test_scan_registry_batch.py`。未改 schema、版本、GUI、service；未 commit、未操作用户真实素材。

- 新增 `register_many(paths, *, source='manual', progress=None, workers=4, batch_size=64)`。返回 `{'items': [(row, is_new)], 'errors': [{'path', 'reason'}]}`。
- 单条与批量登记共用 `_prepare_fingerprint` 和 `_register_on_connection`，保留原有完整指纹、历史版本、人工编辑保护、发现记录、最早入库编号的去重所有权语义。
- 每批最多64项默认值，完整指纹最多4线程；读取前后及提交前校验文件 size/mtime。错误逐项隔离为 error，不把变化或不可读文件登记为成功。
- 一个可复用连接、每批独立事务、逐项 SAVEPOINT；避免每文件连接关闭/提交。Windows 等价路径索引一次构建，其他连接在批次间修改时通过 `PRAGMA data_version` 刷新；依然校验 `samefile`。
- fingerprint/register 回调只在调用线程，注册回调发生在提交后，返回条目读取最终去重分类。
- 每个 hash 任务提交时独立 `copy_context().run`，继承诊断 ContextVar 且任务间不串值。
- 二次审查补齐单条 register 的锁等待变化窗口：获取 DB 锁并开启事务后，与批量复用 `_verify_fingerprint_snapshot` 再次 stat 校验，失败回滚且不登记旧指纹。

## 已运行的验证

1. `rtk proxy python -m py_compile core/media_registry.py`：通过。
2. `rtk proxy python -X utf8 -m pytest tests/test_scan_registry_batch.py tests/test_media_registry.py -q -W error::pytest.PytestUnhandledThreadExceptionWarning`：**21 passed in 3.64s**。
3. `rtk proxy python -X utf8 -m pytest tests/test_media_watch.py tests/test_media_watch_workspace.py -q -W error::pytest.PytestUnhandledThreadExceptionWarning`：**51 passed in 30.39s**。
4. 增加单条锁等待回归后执行 `rtk proxy python -X utf8 -m pytest tests/test_scan_registry_batch.py tests/test_media_registry.py tests/test_scan_progress_gui.py -q -W error::pytest.PytestUnhandledThreadExceptionWarning`：**29 passed in 5.64s**（22项registry及7项真实Qt）。
5. 单条补充 stat 修复后再次运行 watch/workspace 定向回归：**51 passed in 30.05s**。GUI 代理独立复核本修复并运行14项定向测试，**14 passed in 1.56s**，关闭晚变化发现。

测试使用临时真实文件、真实 SQLite、真实 trigger 故障和独立 SQLite 连接：验证输入顺序/完整尾字节哈希/最终去重、重扫人工内容保全、历史补齐、读取期间与提交前真实文件变化、trigger 中断后其他项目提交和历史回滚、进度线程与提交可见性、Windows Unicode/斜杠等价路径、其他连接插入后的索引刷新、线程池最多4线程、并发单条/批量仍只有一个 owner、ContextVar 传播与隔离。Windows 专用用例本次均执行，未跳过。

未运行真实1455素材20秒现场验收；整体基准由主 Agent 执行。未声称 Windows10 实机验证完成。

## service/scan_progress 独立复审（只读）

已读取本轮 `VideoProcessor.get_file_hash`、`extract_thumbnail`、`FileManager.scan_videos`、`scan_and_register_work_scope` 和 `core/scan_progress.py`。

### 已向主 Agent 报告的待修复项

1. **重扫既有内容后的内存缓存失效遗漏**：扫描仅在 `registered` 非空时调用 `invalidate_video_memory_cache()`。已有记录的 file_hash/content_version/summary/status/duplicate_of 可能改变但没有新条目，此时先前已加载的素材列表仍返回旧数据。建议成功 batch items 非空便失效，或明确跟踪变化。需补加载缓存→替换原文件→重扫→读取新版本/已重置分析的测试。
2. **完整指纹与缩略图之间仍存在可检出的文件变化窗口**：缩略图生成前后没有比对登记记录的 file_size/file_mtime_ns。哈希提交后替换文件可能把新画面绑定旧 file_hash；DB 的 id/content_version 条件只覆盖另一个登记者已经推进版本的情况。建议生成前后检查登记快照，不匹配时计错并跳过缩略图写入。文件系统仍无法完全原子防 TOCTOU，不应作绝对保证。

### 通过的静态检查

- `hashlib.file_digest` 读取完整文件，不用采样/mtime缓存替代内容指纹。
- 只有成功提交的新原记录参与缩略图；不对副本重复解码。缩略图解码并发限制16，独立解码器请求一线程，finally释放 capture。
- 缩略图 DB 写入按 id/content_version/path/原记录条件保护，使用批次事务；生成失败计入 error_count。
- 缩略图不再做入库用不到的 base64/phash，未改变 AI extract_frames。
- 缩略图线程使用已有 contextual/in_context 机制传播诊断上下文。
- ScanProgress 的 percent 单调且只在 complete 达1000，阶段完成数独立，100ms节流并强制发阶段/最终事件；回调快照复制后发送。

### 边界说明

目录枚举继承了既有 os.walk 默认忽略目录读取错误的行为；失效 scope 可能得到空结果。若本轮要求“目录读取失败也必须显示错误数”，应给 os.walk 传 onerror 或另行检查 scope。该项不是本轮新增回归。progress callback 抛异常会使扫描任务失败，符合不吞掉调用者错误的常规接口行为。

## GUI/IoWorker 与测试有效性复审

只读复审 `gui/workers/io_worker.py`、`WorkstationView.background_scan_work_scope` 和 `tests/test_scan_progress_gui.py`。本轮7项为5次即时完成竞争测试及成功/失败2项阶段测试，已自行运行通过。

- `on_progress` relay 在 worker.start 前连接，QObject 位于创建任务的主线程，显式 QueuedConnection 保证界面更新只在主线程执行；保留原 int,str progress_updated 信号兼容性。
- 真正启动 QThread，并断言后台执行的线程不是主线程、实际接收回调的线程是 QApplication 线程；快速任务也要求进度先交付、再执行 on_ok，能检测“启动后再连信号”的遗漏。
- 阶段测试使用真实事件循环和 threading.Event 分步推进，分别断言未知总量、不定进度条、分数进度、耗时、数量、禁用控件、成功/异常后的恢复与只成功时重载。不是通过调用槽函数直接伪造线程链路。
- cleanup 在 thread.finished 后清理 _io_worker，再调用最终处理，避免完成回调重载数据时被旧 worker 误判占用。
- 未发现本轮GUI新增明确缺陷。测试仅通过替身提供 service snapshot，不能证明真实目录枚举、完整指纹、SQLite和缩略图的20秒整体结果；该层由 service 集成测试和基准补充。当前为 offscreen Qt 测试，未使用桌面自动化，也不代表 Windows10 实机验收。

## 流水线批次通知追加

主 Agent 明确设计后新增 `register_many(..., on_batch=None)`。每批外层事务提交及释放数据库锁后，调用 `on_batch([(转换后的row_dict, is_new), ...])`。通知保留该批提交时的快照，返回值仍在全部登记结束后读取最终重复分类；不更改原返回契约。回调异常直接向上抛出，不转换成单文件error。

新增3项真实SQLite测试，分别证明独立连接可读已提交记录且可以写入、当前连接不在事务中、另一真实线程可取得DB锁；批次快照与最终重复分类不同且最终返回正确；回调异常在该批已提交后传播并停止后续批次。registry+原registry共 **25 passed in 4.36s**。

## 诊断回归与 PyAV 独立验证

按主 Agent 指示仅修改 `tests/test_diagnostic_feedback.py::test_real_catalog_scan_propagates_context_to_thumbnail_pool`：4个真实临时文件、真实 FileManager/MediaRegistry/DatabaseManager，专用 extract_thumbnail 替身用 Barrier 强制4个真实后台线程同时进入，逐项发 synthetic.thumbnail 后抛 ValueError。保留 operation_id/item_id/4条ValueError 断言，并增强4个唯一失败item_id精确匹配4个synthetic事件；确认4条完整指纹入库记录、4个失败、无缩略图绑定。

- 整诊断模块：**52 passed in 20.59s**。
- 增强失败关联断言及 on_batch 后，诊断定向＋批量registry：**18 passed in 1.73s**。
- PyAV安装版本实时确认 **16.1.0**；requirements 为 `av==16.1.0`。
- 本地 API 文档明确 VideoFrame.rotation 为逆时针角度；当前90→CCW、-90→CW的映射正确。
- 真实创建四象限彩色MP4，经 ffmpeg `-display_rotation` 注入90/180/270显示矩阵，PyAV分别解到90/-180/-90；专用缩略图与OpenCV自动旋转后的首帧形状一致、平均绝对像素误差1.588~1.639。普通0度也正常。
- 通过 patch.dict 强制 av 不可导入，真实OpenCV回退成功并缩放最长边不超过80。
- fixture隔离目录：`C:\Users\A2134\AppData\Local\Temp\scan-pyav-review-saahp08s`。未清理或触及用户素材。
- 静态复查：PyAV容器with关闭、每解码器thread_count=1；失败回退OpenCV且finally释放capture；缓存对成功批次失效，缩略图解码前后及写入前stat检查存在，SQL仍按版本/路径/非副本保护。流水线中缩略图生成与下一批哈希重叠，DB写入安排在registry全部结束后，没有修改完整哈希语义。

已提醒主 Agent：流水线前置变化测试应在 register 阶段回调变更源文件，不能继续依赖 thumbnail 阶段开始事件发生在解码调度之前。已有内容重扫后无图重建已由新调度条件覆盖。未运行1455重型基准。

## 双批有界预取追加

主 Agent 为减少哈希线程等待DB提交的空闲，明确采用下一批预取方案：deque只保存当前批/下一批，初始提交2批；当前批完整哈希→按输入顺序事务提交→释放锁→progress/on_batch完成后，仅补1批。仍最多4个实际哈希线程，待执行future最多 `2 * batch_size`，不一次提交全目录；原完整哈希、前后与提交前stat、逐项错误隔离及最终分类返回不变。

- 新增真实 Barrier(4)+Event 协调测试，首批提交回调阻塞期间下一批已经启动；通过真实Executor提交观察同时证明第三/第四批既未提交也未执行；峰值确实4个hash worker，progress及on_batch仍在单一协调线程。
- 新增预取完成后的真实文件修改用例：在前一批commit callback修改下一批素材，下一批必须被提交前stat拒绝且不入库。
- registry及原registry测试：**27 passed in 4.16s**。已通知GUI作者进行只读独立复审，未跑重型性能测量。
