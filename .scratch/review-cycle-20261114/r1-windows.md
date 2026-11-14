# R1-F Windows、构建、更新与诊断审查

Status: 审查完成，待主 Agent 裁决与修复
Baseline: dd609f8
Branch: fix/review-hardening-20261114

## 已确认问题

### WIN-01 / P2：安装器未限制 Qt 所需 Windows 版本

- 位置：`installer/VideoOrganizer.iss:29` 的 `[Setup]` 区段，未设置 `MinVersion`；运行依赖固定 `PySide6==6.10.2`。
- 触发：在 Windows 10 1809 之前的系统运行安装器。Inno Setup 默认最低系统为 Windows 7 SP1，会允许安装；Qt 6.10 的官方 Windows 支持从 Windows 10 1809（17763）开始。安装器没有阻止明确低于依赖支持范围的系统。
- 证据：本轮直接读取官方 Qt 6.10 支持矩阵，返回 `Windows 10 (1809 or later)` / `Windows 11`；Inno 官方 `MinVersion` 文档返回 `Default value: 6.1sp1`。源码确实无 `MinVersion`。
- 建议：设置 `MinVersion=10.0.17763`；在 README 写清 Windows 10 1809+、Windows 11 x64，避免宣称所有 Windows 10 版本均受支持。
- 边界：没有 Windows 10 实机，未实际运行旧 build 安装器；此项是确定的安装门禁与依赖支持边界缺口，不能写成某个 Win10 实机已复现崩溃。
- 主源：<https://doc.qt.io/qt-6.10/supported-platforms.html>、<https://jrsoftware.org/ishelp/topic_setup_minversion.htm>。

### BUILD-02 / P3：冻结包排除感知哈希计算的必需依赖

- 位置：`build_app.spec:41` 排除 `scipy`；`core/video_organizer_service.py:1545` 的 `calculate_phash()` 调用 `imagehash.phash()`，后者延迟导入 `scipy.fftpack`，异常被捕获后返回空字符串。
- 真实入口探针：`probe_windows_phash.py` 对同一合成 BGR 帧运行真实 `VideoProcessor.calculate_phash()`；源码环境为 `a94d004d004d004d`，仅在当前子进程禁止 scipy 导入后为空字符串。没有重写哈希算法，没有修改产品源码。
- 包证据：现有 `.release-build/project-review-rc10-20261114/work/build_app/warn-build_app.txt:299` 记录 `missing module named scipy.fftpack - imported by imagehash (delayed)`；同一构建的 PYZ 有 imagehash 无 scipy。
- 影响：安装版无法生成该已实现的感知哈希字段。当前重复素材判断使用全文件内容哈希，不依赖单帧 phash，因此此项不能夸大成“安装版去重失效”，也未发现对应用户入口崩溃。
- 建议：若保留感知哈希能力，解除 scipy 排除并添加冻结包功能探针；若产品明确不需要该字段，另行决定是否正式移除此遗留能力，不能本轮擅自改变产品范围。

## 当前验证

1. 定向测试：

   `rtk proxy python -X utf8 -m pytest tests/test_build.py tests/test_software_update.py tests/test_software_update_gui.py tests/test_update_mirror.py tests/test_verify_windows_qt_bundle.py tests/test_smoke_exit.py tests/test_diagnostic_feedback.py -q -rs`

   结果：**137 passed in 39.30s**，没有跳过。覆盖签名/摘要、版本/渠道、回环 Qt HTTP、镜像兜底/禁止条件、取消清理、离线包、安装门禁、更新会话恢复、诊断脱敏/有界性/故障处理、构建输出隔离及 DLL 检查器。

2. 当前本机 DLL 重测：

   `rtk proxy python -X utf8 tools/verify_windows_qt_bundle.py .release-build/project-review-rc10-20261114/dist/VideoOrganizer`

   结果：`Qt6Core.dll OK`、`Qt6Gui.dll OK`、`Qt6Widgets.dll OK`。这是现有本地包在当前 Windows 11 的加载证据；本代理没有重新打包，也未据此宣称 Win10 通过。

3. 依赖可解析性：

   `rtk proxy python -X utf8 -m pip install --dry-run --ignore-installed --python-version 3.11 --platform win_amd64 --implementation cp --abi cp311 --only-binary=:all: --report .scratch/review-cycle-20261114/deps-win311.json -r requirements-dev.txt`

   Python 3.12 使用同命令，替换 `3.12` / `cp312` / `deps-win312.json`。

   两次均退出码 0，当前索引可解析全部固定直接依赖及传递依赖的 Windows x64 wheel；未安装或升级当前 Python 环境。3.11 解析 scipy 1.17.1，3.12 解析 scipy 1.18.1，因此本清单并非传递依赖完全锁定。依赖报告保存在本目录，只有公开包元数据。

4. 感知哈希探针：

   `rtk proxy python -X utf8 .scratch/review-cycle-20261114/probe_windows_phash.py`

   返回 `{'source_phash': 'a94d004d004d004d', 'excluded_scipy_phash': ''}`，断言通过；这是证明缺口的探针通过，不代表产品功能通过。

## 平台验证矩阵

|环境/功能|本轮证据|结论与限制|
|---|---|---|
|Windows 11 / Python 3.13.12 / Qt6.10.2|137项定向测试、本机Qt包DLL加载|对应范围通过；可见GUI和全量套件由主Agent记录|
|Windows 10 1809+ x64|Qt官方支持；源码平台分支和模拟Windows标志测试|未取得Win10实机/VM，不可报告稳定性实测通过|
|Windows 10 <1809|Qt官方不支持；当前安装器缺门禁|WIN-01，需明确最低支持版本|
|Python 3.11/3.12 x64|各自wheel解析dry-run成功|仅安装依赖可解析；没有对应解释器运行时测试|
|当前源码再次完整构建/新安装/覆盖安装|未执行|避免与主Agent重复大型构建；历史安装记录不替代本轮证据|
|公网GitHub及固定镜像|更新传输回环测试与源地址规则审查|未执行真实公网发布/下载稳定性验收|
|Win10/11标准用户/权限拒绝/杀毒占用/多显示器|部分错误路径替身测试|未有各环境实机证据，不宣称全组合覆盖|

## 搜索范围及工具限制

已读 requirements、build.py、build_app.spec、Inno脚本、build_installer、smoke_installer_update、Qt验证器、software_update、update_runtime、background_process、update_controller/network、software_update面板、diagnostic_logging/bundle、相关测试及 ADR-0008/0009/0010。另检查主窗口安装/退出门禁。没有发现可确认的更新信任链绕过、下载渠道交互竞态或后台退出崩溃；不以此保证所有线程/网络时序。

当前会话的工具目录没有 Context7 能力，无法调用 `resolve-library-id` / `query-docs`。涉及平台和安装CLI事实时退回官方一手网页，以上链接均为本轮现场读取，不凭训练知识假称已查文档。

仅新增本报告、独立探针与依赖解析报告；未修改产品/现有测试，未读取用户凭据或真实素材，未运行生产安装器、提交、发布或删除目录。
