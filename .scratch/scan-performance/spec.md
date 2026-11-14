# 1455视频扫描性能与细粒度进度

状态：性能、冻结包、完整回归与显式压力全部通过，待用户现场验收批准
分支：fix/review-hardening-20261114

用户要求在截图设置（分析并发128、图片准备512、入库缩略图512、8帧、质量80、场景/转录/自动XMP关闭）下，1455视频扫描从近两分钟降到20秒以内，增加更细进度。版本保持0.0.1-rc10；无用户明确批准不迭代，不提交，不桌面自动化。现有审查/重命名修改保留。

范围：工作台浏览/累加及启动复用的扫盘入库链路。先测目录枚举、完整指纹、SQLite登记、去重、缩略图和UI就绪。保留ADR0013全文件指纹及最早编号所有权，不用抽样哈希或跳过真实工作冒称完成。原素材只读，生成物和数据库均隔离。用户已确认目录D:\临时\新建文件夹，首次入库；1455视频约17.6GB。

- [x] 读取规则、ADR、现有扫描/注册/缩略图/GUI后台代码
- [x] 1455规模真实解码/文件/SQLite合成与原素材基线及热点测量
- [x] 明确性能方案和进度协议，限定两名已有代理协作
- [x] 实施及数据身份/线程/进度回归，156项相关测试通过
- [x] 同条件20秒预算验证和独立复审，三轮Qt首屏16.976/18.021/18.221秒
- [x] 更新结果、限制、正式安装器和校验值，交付用户现场验收
- [ ] 用户确认无bug并批准后再commit

初步代码观察（尚非性能结论）：新路径Windows兼容查找每项遍历全库；每项登记打开/关闭SQLite；完整文件哈希用4KB Python循环；每次扫描均重算每文件哈希；缩略图抽帧同时生成未使用base64/phash且写原尺寸图。需实测区分主因。

基线：benchmark.py --profile，1455合成MP4/50组重复，诊断启用，36.389秒；1455条记录、50原记录/50可用缩略图。profile显示1455次注册30.44秒，连接提交10.71秒、关闭10.60秒，Windows兼容路径path_key约105万次4.54秒。完整记录.release-build/scan-performance/baseline-867a4a91。

实施契约：

1. registry新增register_many(paths, *, source='manual', progress=None, workers=4, batch_size=64)，返回{'items': [(row,is_new)], 'errors':[{'path':...,'reason':...}]}。准备阶段有界线程完整哈希（保留现有可注入hasher），每文件stat前后+提交前重查，变化/不可读明确error不登记；按输入顺序登记稳定原编号，事务64项批次及逐项savepoint，复用一个连接，Windows路径等价索引只构建一次并验证samefile，不改schema。复用register持久化核心避免两套业务语义，单条接口保留。回调progress(phase,done,total)只在调用线程（phase=fingerprint/register），单调累计；最后输出row必须反映最终重复分类。不能用size/mtime或硬链接替代全字节哈希，不改AI/watch/外部更名语义。
2. 主Agent负责service.scan_and_register_work_scope(progress_callback=None)：enumerate→历史指纹→register_many→仅新原记录缩略图→完成；生成JSON兼容snapshot: phase,phase_label,completed,total,percent(0..1000),elapsed_seconds,files_found,new_count,duplicate_count,error_count。阶段百分比权重不冒充时间预测，事件每100ms或阶段/最终强制；枚举未知总量total=0且显示数量，指纹/登记合计5..75%，缩略图75..99%，全部完成才100%。仅成功提交入registered；失败计数和记录明确。
3. 主Agent负责FileManager.scan_videos可选progress_callback(done_count)以及VideoProcessor.get_file_hash改标准库hashlib.file_digest实现全文件MD5；入库缩略图专用方法只解第一帧、先缩放后编码WebP/JPEG，不计算无用base64/phash，不改AI extract_frames；bounded并发有效上限min(用户设置,16)，OpenCV每解码器请求单线程（兼容不支持的后端），防512个解码器争抢资源。复用线程上下文/脱敏，写DB按id+content_version保护。
4. GUI代理只改workstation、io_worker及新GUI测试：run_io_job新增on_progress可选回调且在start前连接worker.progress_updated（必要可新增object scan信号，不改既有int,str信号契约），后台闭包仅emit snapshot，Qt主线程接收。总/阶段双条、完成数/总数/耗时，完成/失败恢复控件。现有无参扫描替身测试按新增可选回调同步，不能弱化断言。

分工：review_storage仅media_registry及新registry tests；review_gui仅gui/views/workstation.py、gui/workers/io_worker.py及GUI测试。主Agent service/processor/benchmark合并验证。最后交换独立复审，不新建代理，不重叠编辑。失败修复局限本轮改动，禁止批量删除、生产数据或版本变更。

追加方案与测量：旧源码快照真实目录不带profile为144.979秒，1455记录/1355原/100副本/1355缩略图；第一轮OpenCV有界方案39.163秒，进一步定位open全局锁。仅实验环境禁锁可降低缩略图阶段27.65到8.22秒，未采用修改进程全局锁的方案。引入成熟PyAV16.1.0，独立容器/单解码线程，首帧先缩放后编码、保留旋转和OpenCV回退：service16.772秒、含Qt首屏19.764秒。为保留余量增加register_many可选on_batch，已提交释放锁后立即调度本批缩略图，与下一批完整哈希重叠；所有缩略图落库仍等最终登记完成后，SQL版本/路径/原记录保护，文件在解码前后、收集结果和获得DB锁后均stat校验。流水线首轮含GUI首屏15.535秒。

独立复审修复：单条登记等待DB锁后重查stat；重扫已有内容变更刷新L1缓存；生成前后和绑定前素材变化拒绝绑定；重扫缺图已验证原记录可补图。登记、GUI线程交付、真实service集成、4条真实诊断线程关联测试均已加入，最终全量和冻结包待验证。版本仍0.0.1-rc10，用户批准后才commit。

最终产品加固：缩略图flush先BEGIN IMMEDIATE取得SQLite写锁再stat，避免只取得Python锁后等待其它进程写事务的变化窗口；同一真实独立SQLite写锁测试，隔离源码移除该行时精确红，最新实现绿，未改变5秒监控测试超时。156项相关联合回归全绿；此前启动的全量1150passed/1failed/9skip（904.50秒），唯一监控暂停恢复test的5秒等待超时，独立两参数2passed；登记代理只修复该模块diagnostic/metrics临时目录隔离，等待最终回归。

冻结包验证：全新package-20261114-rc10-scan-verified构建成功，13个已修改业务模块PYZ匹配，额外核对新scan_progress及rc10版本。Qt三DLL可加载，1251文件/520378632字节无配置或运行数据污染。独立真实EXE启动恢复1455素材：catalog.scan13.011秒，进程启动到观察扫描完成约17.711秒；1455完整指纹/编号/重复归属与旧基线一致，1355图逐张解码，无ERROR事件。单独正常启动/正式退出exit0、1条合成数据及设置保留。首次smoke共享诊断目录与全量并发导致诊断告警弹窗超时，核对后仅结束其自身隔离进程；改child LOCALAPPDATA到专用临时目录后通过，未改产品回避问题。

正式安装器已编译：144042560字节，SHA256=ae62ce8881b8b9af738a86c35945a9ca12f2049a62d279b0e9cf1846e1c2c148；未运行安装器/覆盖生产/上传，版本保持0.0.1-rc10。

最终完整回归：隔离LOCALAPPDATA，1153passed、0failed、9显式压力skip，789.46秒，76条既有Qt弃用提示。包含所有最终改动。显式VOP_RUN_STRESS=1压力9项另行执行，未用旧压力结果冒充本次通过。

最终压力：独立LOCALAPPDATA、VOP_RUN_STRESS=1，9passed、0failed，284.88秒。常规1153与显式压力9合计1162项通过，实际分两次执行。产品版本/包/源码保持冻结，无生产实例操作、无Windows10实机、无桌面自动化、未commit。详见docs/reviews/2026-11-14-scan-performance.md。
