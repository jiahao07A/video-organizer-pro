# 性能诊断与验证

Status: claimed
Issues: #58 / #59 / #60
基线：4c5625b（rc3发布交接）；当前分支fix/58-startup-analysis-performance。未提交或发布。

## 反馈循环与结论

- `python -m pytest tests/test_runtime_performance.py -q`先出现5项失败：启动导入调用FFmpeg、32线程创建32个客户端、两页摘要调用全量路径、缺少批量提交入口。修复后通过；后续空预览提前初始化媒体设备的失败回归也通过。
- `.release-build/perf-startup-audit.py`捕获scenedetect.video_splitter导入时的ffmpeg -v quiet。此流程只用于检测视频分割工具，本应用启动不需要它。场景检测延迟到实际分析时加载；依赖探测以及音频转录/元数据所需FFmpeg子进程隐藏Windows控制台，不隐藏安装向导。
- `.release-build/perf-interaction-baseline.py`在相同合成4000视频/500标签下测量：导入16.222秒，1500次逐条提交、1000次add_tag；优化后0.143秒（包括备份/后台写入/模型刷新）。不包含人工向导或真实AI推理时间，不能声称500词AI分类仅0.143秒。
- 摘要刷新原平均0.058秒→0.000022秒，不再在UI线程展开全量路径。实际操作快照入口未变，新增分页只加载2条但操作覆盖完整7条的回归。
- `.release-build/perf-view-construct.py`：素材库页面构造1.438秒→0.024秒，原1.396秒来自空预览初始化QMediaPlayer/QAudioOutput。现在首次有效素材选择时初始化，空面板不启动媒体设备；播放/清空释放测试通过。
- `tests/test_analysis_concurrency.py`真实SDK+本地HTTP屏障：32条任务达到32个同时在途请求，32成功。`.release-build/perf-original-concurrency.py`注入rc3原网关缓存方法，仍32在途/32成功；没有证据支持固定串行/假并发。
- 发现真实客户端缓存竞态：首次32任务构造32个SDK客户端。加锁只保护构造和缓存，网络调用在锁外；现在仅创建一个共享连接池。
- 原测试视频只读抽8帧，场景检测1.873秒，均匀抽帧0.242秒；不写缩略图，不请求真实AI。单样本说明前置准备开销，不代表4000视频全体或供应商吞吐已验证。

## 数据与失败保护

批量导入：校验组/冲突/别名目标→一致性快照→单个SQLite事务→配置投影原子保存→一次模型刷新。保留已有ID、颜色、英文名、图标等属性；DB事务失败回滚，备份失败不写，投影保存失败明确报告已写DB与备份位置，不显示成功或自动恢复覆盖新数据。UI有忙碌提示，提交阶段禁止Esc关闭，相关后台线程可被既有安装门禁识别。

没有修改用户素材、生产DB或API设置；合成DB和探针在忽略路径，未提交。没有新增依赖或改变设置键/默认值/解码上限。两类TXT导入和初始化投影复用批量事务，减少逐词重复同步。

## 测试与构建

- 最终完整回归507 passed，139.86秒（25条既有invalidateFilter弃用警告）。随后仅补跨组多语言定义保留，相关runtime专项18 passed；没有扩展真实供应商压力测试。
- compileall、git diff --check、Qt Core/Gui/Widgets打包检查通过。
- `.release-build/perf-candidate-dist/VideoOrganizer`最终独立PyInstaller构建完成，包含跨组定义保留修正；未调用build.py、未删除旧目录。仍使用未更改的rc3版本标识，属于本地验证构建，不公开发布或替换rc3资产。
- `.release-build/perf-packaged-startup.py`在独立数据目录启动真实打包EXE，观察8秒：无子进程、无可见ConsoleWindowClass，正常退出码0。数据/定位记录只写测试包及测试目录。
- 最终candidate重复该启动审计通过，证据位于`.release-build/perf-candidate-startup-data/startup-evidence.json`；`tools/verify_windows_qt_bundle.py`检查Core/Gui/Widgets均通过。候选包使用独立测试库，不能把它的空库视为生产数据丢失。
- 用户验收与commit批准仍待完成；不沿用上一版本发布授权。

## 追加子代理评审与压力测试

用户明确授权后，使用code-review技能安排独立规范轴、需求轴评审，另由独立子代理编写并运行本地压力测试。比较基线为4c5625b，包含工作区未提交与新增文件。

- 规范轴：P3分支命名未含议题编号；主Agent核对规范后已改为`fix/58-startup-analysis-performance`。独立runtime+gateway测试44项通过，无阻断代码发现。
- 需求轴：P1跨组导入在配置投影失败后会回灌旧词。主Agent修复：在导入同一事务写`storage_metadata`恢复记录，DB提交后立即更新正确内存，启动在AI初始化前恢复DB投影；配置仍不可写也不使用旧词。
- 再次复审：P1恢复记录可能覆盖随后正常保存的新组规则。主Agent修复：统一保存路径在`STORAGE_IO_LOCK → db.lock`内先刷新恢复记录，再写文件，成功后清除记录；失败保留最新规则。
- 最终需求轴复审通过，无新增可操作发现；子代理独立runtime专项23项通过，主Agent也独立运行23项通过。覆盖事务失败无标记、同进程重载、重启成功/失败、DB提交后中断、后续规则保存成功/失败。
- 压力测试使用真实SDK及127.0.0.1服务，首次9项通过，改善测试自身日志隔离与客户端收尾后最终9项通过（107.89秒）。各阶段网络在途来自服务端计数，不用线程数代替。生产上限仍32，超上限只测试网关。

| 场景 | 请求数 | 峰值在途 | 结果 |
| --- | ---: | ---: | --- |
| 应用32并发×512，两轮 | 1024 | 29 / 20 | 1024成功、无丢失 |
| 网关64并发×512 | 512 | 64 | 512成功、一个客户端 |
| 网关128并发×512 | 512 | 128 | 512成功、一个客户端 |
| 网关256并发×512 | 512 | 256 | 512成功、一个客户端 |
| 429故障、一次重试 | 256 | 6 | 128业务调用最终成功 |
| 500故障 | 128 | 8 | 128明确失败，遵守既有不重试策略 |
| 0.1秒SDK超时 | 32 | 32 | 32有界失败 |
| 32在途+224排队后取消 | 32 | 32 | 在途完成、224未发送 |

256场景测试进程RSS峰168.46MiB、线程峰544（包含本地服务端）；各网关场景收尾线程恢复30。两轮应用没有屏障，实际峰值低于32不表示假并发。网关屏障才验证精确64/128/256同时在途。没有证明长期无内存泄漏，不据短时RSS保留判定泄漏。

压力脚本为`tests/test_analysis_stress.py`，显式设置`VOP_RUN_STRESS=1`执行，默认回归跳过；子代理详细脱敏JSON已单独保存在忽略路径`.release-build/perf-stress-subagent-results.json`。全部合成输入，未验证真实视频解码、外部供应商限流或推理吞吐，未调用真实收费API。初轮少量合成诊断日志位于既有忽略目录，后续已隔离到临时目录，没有删除产物。

规范轴最终复审同样通过，独立执行恢复相关6项测试通过，未发现反向锁或新死锁。主Agent修复后完整回归`rtk python -m pytest tests -q`：512 passed、9 skipped、25条既有Qt弃用警告，106.77秒；9 skipped为要求显式启用的本地压力场景，另行执行，不能当作失败或未测场景隐去。

主Agent随后独立复跑：`rtk proxy pwsh.exe -NoProfile -Command '$env:VOP_RUN_STRESS="1"; python -m pytest tests/test_analysis_stress.py -q --tb=short --disable-warnings'`，9 passed，98.49秒。最新JSON为`.release-build/perf-stress-results.json`。应用两轮1024全成功，峰值27/17；网关64/128/256各512全成功、精确达到对应在途数且各一个客户端；429/500/超时/取消与上表相同结论。256场景RSS峰168.14MiB，网关收尾线程均30。主Agent已检查新增测试和实际JSON，最终`git diff --check`通过。

前述candidate打包启动证据是在追加评审修复之前取得；该包不含本节的新恢复保护，不能作为最终修复交付包。尚未commit或发布。

## rc4安装器交付

### 最终发布验证

用户明确授权review、打包并发布rc4。最终规范轴与需求轴独立review均无阻断项，分别独立通过50项和63项相关测试。主Agent最终完整回归513 passed、9显式压力测试skipped、25条既有Qt弃用警告，148.31秒；随后仅补3项512设置保存重载/执行器与抽帧闸门上限验证，runtime专项26 passed。测试确认上限512配置真正生效，不将其视为512真实解码压测。新增测试初次误用不存在的SettingsView.save_settings方法已改为实际apply_settings，相关测试通过；不属于产品失败。

最终发布使用`.release-build/rc4-release-final-*`全新路径，包含最新image_url.url提示修正。此前本地rc4包不作为公开资产，旧目录全部保留。发布限制详见release-notes.md。

用户指定版本`0.0.1-rc4`并要求本地EXE安装器；唯一版本源`core/app_version.py`已同步，未创建commit/标签或发布Release。

随后用户要求相关并行设置上限统一调整为512。`processing.max_workers`、`processing.extract_parallel`、`processing.extract_workers`的GUI范围和服务层硬顶均已统一为1～512，默认值不变；文档同步说明高并发会争用CPU、磁盘和内存。设置/持久化/runtime专项51项通过，`git diff --check`通过。rc4目录包和安装器将用该上限调整后的源码重新构建。

- 使用全新`.release-build/perf-rc4-reviewed-work`与`perf-rc4-reviewed-dist`构建，保留此前产物，未调用会清理目录的build.py。
- 版本变更后的更新相关测试71 passed；Qt Core/Gui/Widgets打包检查通过。
- 包体审计：1084文件，365891483字节；无用户配置/数据库/日志/素材，EXE为GUI子系统。直接读取打包PYZ确认APP_VERSION为rc4、包含background_process及最终投影恢复函数。
- Inno Setup标准安装器生成成功：`.release-build/perf-rc4-reviewed-installer/VideoOrganizer-v0.0.1-rc4-setup-win-x64.exe`，104613941字节。
- SHA-256：`CBA596EA034033578C4F763D72A0954838A48BDB0E079D9335CEA1D61407447F`；同目录提供`SHA256SUMS.txt`及`build-info.json`。
- 同一目录包另构建isolated-smoke变体，仅用于验证，不注册卸载项/不创建快捷方式；隔离安装验证通过，不触碰用户安装目录或素材库。结果：installed_version=0.0.1-rc4、startup_exit_code=0、update_session=completed、custom_data_preserved=true、record_count=1、reinstall_preserved_settings=true；证据`.release-build/perf-rc4-installed-check/result.json`。
- 安装后的真实EXE另进行启动窗口审计：8秒观察，无子进程，无可见ConsoleWindowClass，正常退出码0；证据`.release-build/perf-rc4-startup-data/startup-evidence.json`。定位记录只写隔离安装目录，标准安装器输入目录保持不含测试定位文件。
