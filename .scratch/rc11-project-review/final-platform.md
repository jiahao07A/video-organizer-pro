# 最终冻结包验收执行包

Status: claimed

由原 platform 代理执行，本次用户已授权多代理闭环；复用现有代理，不新增。源码已冻结，禁止修改产品、测试和构建脚本，禁止提交。

## 已确认现状与方案

分支 codex/rc11-project-review。第三轮所有缺陷已交叉通过。主审原生退出探针证明 QTest.qWait 连续阻塞 Python 缩略图线程，换成 processEvents + time.sleep 后两秒任务清空且 close=True；无需产品修补。接下来主审同时执行最终全量测试。现有 RC11 安装包不包含本轮改动，必须重建后重验。

1. 执行 `rtk proxy python -X utf8 build.py --output-root .release-build/review-rc11-final-build`，日志可重定向到本轮新文件。若目录已存在停止并改用全新明确目录，不清理。
2. `rtk proxy python -X utf8 tools/verify_windows_qt_bundle.py .release-build/review-rc11-final-build/dist/VideoOrganizer`，检查返回码和 Qt 插件。
3. 用 tools/build_installer.py --iscc `C:/Users/A2134/AppData/Local/Programs/Inno Setup 6/ISCC.exe` --dist `.release-build/review-rc11-final-build/dist` --output `.release-build/review-rc11-final-installer` --isolated-smoke。
4. 用新生成的 isolated-smoke.exe 调 tools/smoke_installer_update.py --installer <完整路径> --output `.release-build/review-rc11-final-installed`；验证退出0、更新核对完成、两次安装后设置和记录保留。
5. 复制 `.scratch/review-cycle-20261114/frozen_phash_probe.py` 到 `.release-build/review-rc11-platform/final_frozen_probe.py`（两者 REPO parents[2] 均正确）；只在本轮 probe 修改必要产物路径。查看 argparse 后对新 dist 运行 --metadata-injection --stop-after-probe。预期真实 EXE→watchdog→合成视频→HTTP→SQLite→缩略图、指纹、帧一致，内嵌元数据成功。probe 停止自己的进程不作为正常退出证据，第4步独立验证正常退出。
6. 可复跑原 public_update_probe.py 检查公网签名；不下载发布、不使用个人API。
7. 结果追加此文件，含各退出码、产物大小/SHA、命令与JSON证据、实际缺口。不可把Win11作为Win10。不得操作真实安装、配置、素材；不卸载，不批量删，不修改系统功能，不发布。

全程 pwsh.exe + rtk。出现构建/验收失败保留产物和日志，明确失败证据后报主审，不自行扩展修复范围。

## 验收记录（进行中）

- Python 3.13.12 / Windows 11 x64 build 26200，`build.py --output-root .release-build/review-rc11-final-build` 退出 0，约 142 秒。目录包 1,253 文件、608,099,576 字节；EXE 19,934,817 字节，SHA256 `a858b2828372bf88aec9b3a7fc1cd9fb5d4851e550b299ab1cc615c2e55333e2`。
- Qt6Core/Gui/Widgets DLL 实际加载均成功，qwindows、ffmpegmediaplugin、windowsmediaplugin 等存在。两份归档（work PYZ 与 EXE 内 PYZ）24 个关键模块与当前冻结源码字节码一致。包中未带设置、数据库、定位器、日志或私钥；证据 `.release-build/review-rc11-final-build/verified-bundle.json`。记录中的 source_commit 是工作区基线 f917ddf，实际新改动尚未提交，不能将其当成发布 tag 构建。
- 首次隔离安装器编译退出 1（ISCC 为 2）：`installer/VideoOrganizer.iss:110 'BEGIN' expected`。新注释仍位于 Pascal [Code] 段而误用分号，已发回主审路由 desktop 修复；失败输出与日志保留，不覆盖重用。
- 本轮冻结 EXE→watchdog→真实合成 MP4→SDK HTTP 回环→SQLite→缩略图/感知哈希→内嵌元数据验证退出 0，3.515 秒。一个请求发送两帧图片，12 个解码帧写回前后哈希一致；ffprobe comment、数据库成果和新文件指纹一致，内容版本与监控阶段同步，邻接哨兵未动，临时文件无残留。证据 `.release-build/review-rc11-platform/frozen-phash-20261121-150843-8dbc47dc/result.json`。核验路径后终止自己的测试进程，不能将终止作为正常退出证据；安装验收另行执行。探针放入 dist 的唯一定位器已移到测试记录目录，复核产物无数据污染后才可重新打安装器。
- 额外的真实公网更新检查本轮失败：GitHub 错误后固定镜像返回 HTTP403；与首审本日成功的记录分开保留。未改变网络策略、未上传或发布。Windows 10 环境仍不可用，不报告实机通过。

## 最终安装验收完成

- desktop 修正后已核对 [UninstallDelete] 在说明注释之前。独立重新执行 `tools/build_installer.py`，输出全新 `.release-build/review-rc11-final-installer-b`，返回 0。仅生成 isolated-smoke 测试安装器，不是待发布产物。
- 测试安装器为 `VideoOrganizer-v0.0.1-rc11-isolated-smoke.exe`，166,647,480 字节，SHA256 `8a16454bfdfc4ce2f130a75a54b19c0e852c39f52f16c74cd5423cbaa4b97cbc`。证据 `.release-build/review-rc11-platform/final-installer-evidence.json`。
- 在独立 LOCALAPPDATA 下执行 `tools/smoke_installer_update.py --installer .release-build/review-rc11-final-installer-b/VideoOrganizer-v0.0.1-rc11-isolated-smoke.exe --output .release-build/review-rc11-final-installed`，返回 0。两次安装均正常完成，中间真实已安装 EXE 正常退出码 0，更新核对 completed，自选管理数据目录、1 条 SQLite 记录、设置标记完整保留，未建立意外的新空库。证据 `.release-build/review-rc11-final-installed/result.json` 及 install-1/2.log。
- 安装后的 EXE SHA256 与冻结目录包相同；Qt DLL 加载再次通过；无残留 VideoOrganizer 进程。最终产物目录再次校验 24 个模块与当前源码一致，无测试定位器或配置混入。
- 本代理只修改本轮探针和验收记录，未改产品或测试、未提交、未安装到真实应用目录、未发布、未卸载。

## 安装器编译回归闭环

主审已确认 ISCC 110行 BEGIN expected。desktop 按 round4-installer.md 仅移动注释到 [UninstallDelete] 内。platform 观察 .iss 注释位置修正后，用新目录 `.release-build/review-rc11-final-installer-b` 独立重编，继续原隔离安装验收。EXE源码未变，无需重复PyInstaller。

## 第四轮GUI修复后的最终构建授权

主审已核对 gui/widgets/detail_panel.py 仅四按钮改用已有FlowLayout，和对应响应测试两文件变化。103相关测试、两个原生尺寸/两个页面独立复核通过。195文件 frozen-source.json 已更新并保留 before-layout 清单。现在开始最终新构建：输出 review-rc11-final-build-b、安装器 review-rc11-final-installer-c、安装测试 review-rc11-final-installed-b。执行同样PyInstaller、Qt、隔离双次安装和正常退出验收；归档源码核对须包含 gui.widgets.detail_panel。冻结媒体探针在新dist再测一次并移出其唯一定位器，安装器前核对无数据污染。上轮公网403无需再重复请求。所有产品源码继续只读，完成结果追加此处并区分此前产物。

第四轮构建记录：PyInstaller 输出 review-rc11-final-build-b，184.951 秒、退出 0。进行中收到第五轮 UI 复修通知，未启动安装器编译或安装验收；本目录仅为中间产物，不能当成第五轮最终包。

## 等待第五轮预览区修复

主审最新截图确认预览控件在小窗口中被表单遮挡，已按 round5-preview.md 交api修复、desktop复核。暂停尚未开始的安装器/安装流程；已在运行的构建可完成并标为第五轮之前证据，不删除。等待主审再次冻结后用全新目录构建，避免验收过期包。

## 第五轮最终冻结：开始验收

主审已检查第五轮源码diff、真实屏幕彩条播放截图和表单截图：四场景鼠标/滚轮/播放/倍速/保存/正常退出均通过，水平滚动max0。相关150测试通过。195文件 frozen-source.json 已更新，当前无源码变化计划。

现在授权 platform 执行最后验收，输出 `.release-build/review-rc11-final-build-c`、`.release-build/review-rc11-final-installer-c`、`.release-build/review-rc11-final-installed-b`。其余步骤与原执行包相同：新PyInstaller、Qt DLL、归档源码一致性（必须含 detail_panel/media_preview）、真实冻结媒体验收及唯一定位器移出，再编 isolated-smoke 安装器、两次安装和正常退出检查。最终汇报这组三目录、大小和SHA；勿混用旧包。主审同步最后全量测试，禁止改源码和提交，无需再次请求公网更新。

### 第五轮最终结果：全部本地验收通过

- 最终有效目录组合为 `review-rc11-final-build-c` / `review-rc11-final-installer-c` / `review-rc11-final-installed-b`，前几轮只作历史证据。
- PyInstaller 116.852 秒退出 0，Qt DLL 全部加载通过。两份归档 26 模块与最终源码一致，包含 `gui.widgets.detail_panel` 和 `gui.widgets.media_preview`。目录包 1,253 文件、608,099,964 字节，最终 EXE 19,935,205 字节，SHA256 `583e44f3ce28e3a3244dfa62fc9f17cb42fa37cdc28af6d1f3e6cd30f58a712a`。证据为 build-c 下 `verified-bundle.json`。
- 最终冻结媒体探针再次退出 0，3.011 秒、1 次真实回环 HTTP、2 张 JPEG 输入，SQLite 分析与 320×240 缩略图正常，12 帧写回前后完全相同，版本/指纹/监控/旁路哨兵均通过；证据 `.release-build/review-rc11-platform/frozen-phash-20261121-154315-38d5d224/result.json`。核验进程完整路径后终止自己的探针进程，无残留。唯一测试定位器单文件移出 dist，再次核对无个人数据后编译安装器。
- 最终 isolated-smoke 安装器编译退出 0，文件 `VideoOrganizer-v0.0.1-rc11-isolated-smoke.exe`，166,653,382 字节，SHA256 `9ecb58d364b207441786eeb4e1d2bc2da10e614ff351a81c13744b57cd5fad56`；证据 `.release-build/review-rc11-platform/final-installer-c-evidence.json`。
- 最终双次隔离安装脚本退出 0，安装版原生 EXE 正常退出码 0，更新核对 completed；自选目录、1 条 SQLite 记录和设置保留，无意外空库。安装 EXE 与 build-c 字节摘要一致，安装后的 Qt DLL 再次通过，无残留进程。证据 `.release-build/review-rc11-final-installed-b/result.json` 和 install-1/2.log。
- 此包是未经用户提交/发布批准的隔离验收包，不等于已经发布新版；本轮未再请求公网。Win10 尚无实机环境，不能由上述 Win11 结果替代。
