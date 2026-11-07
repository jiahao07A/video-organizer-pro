# 更新机制验证记录

日期：2026-11-07
Issue：#51
分支：feat/51-software-update
当前基线：43ccb7f；功能改动尚未提交，按 AGENTS.md 等待用户确认无 bug 并批准。

## 自动验证

- `python -m pytest tests/test_software_update.py tests/test_software_update_gui.py -q`：41 passed，4.76 秒。
- 更新与既有 GUI 回归组合：72 passed（后续补充的 4 项更新测试另行通过）。
- 完整 `python -m pytest -q`：452 passed、1 failed，82.31 秒；之后仅增加 4 项测试及发布/冒烟工具与文档，没有再改变应用代码。
- 失败项：`tests/test_startup_upgrade_gui.py::test_startup_upgrade_dialog_confirms_and_reports_progress`，等待对话框完成超时。
  该项为 README/既有发布记录中已知限制；本轮早期完整套件曾 443 passed（当时尚未加入全部新增测试），不能据此隐藏最终失败。
- `python -m compileall -q core gui tools tests video_organizer_app.py` 及 `git diff --check`：通过。
- 真实 Qt HTTPS 请求 GitHub Releases：完成，未发现比当前版本更新且有完整资产的适用版本。
- Qt 本地 HTTP 测试覆盖完整下载、响应超限、HTTP 429、下载取消和不可信重定向；离线包与缓存复验覆盖签名、摘要和大小错误。
- 数据保护测试覆盖备份失败、空间不足、原库缺失、其他实例、未保存设置取消/保存失败、任务门禁及安装进程启动失败。

## 构建、签名和隔离安装

- Python 3.13.12、PyInstaller 6.19.0、PySide6 6.10.2、Inno Setup 6.7.3。
- 更新直接依赖：packaging 26.0、cryptography 49.0.0、psutil 7.2.2。
- 最终目录包：`.release-build/update-51-final-dist/VideoOrganizer/`；使用独立 distpath，保留所有既有产物。
- `tools/verify_windows_qt_bundle.py`：Qt6Core/Qt6Gui/Qt6Widgets DLL 加载通过，无系统 ICU 同名库污染。
- 产物隐私审计：无个人设置、标签配置、数据库、视频或私钥；certifi 公共 CA 证书允许随依赖打包。
- 私钥存放仓库外，ACL 仅允许当前 Windows 用户完全控制；客户端仅内置公钥。
- 最终测试安装包与签名资产：`.release-build/update-51-delivery/`。
- 安装包：`VideoOrganizer-v0.0.2-rc1-setup-win-x64.exe`，104604745 字节。
- SHA-256：`4992eb527dbe7b19b2a757a403d1f1e0fccf3abc1dc376457a21548196357fb6`。
- 使用真实私钥通过发布工具生成清单并验签、回验安装包摘要；私钥未加入资产。
- 隔离冒烟安装器使用独立 AppId、不注册卸载项、不建快捷方式；文件名含 `isolated-smoke`，禁止公开发布。
- 实际运行 `tools/smoke_installer_update.py`：安装至隔离 application 目录，使用自选 custom-data 目录；
  预置一条合成素材、设置标记及更新前备份，写入待核对会话后启动真实 EXE，确认会话 completed，正常 WM_CLOSE 退出码 0。
- 再次覆盖安装后，素材记录数仍为 1，设置标记保留，未创建 application/data 空库。
- 证据：`.release-build/update-51-installed-check/result.json` 和 `install-1.log`、`install-2.log`。
- 中文界面预览：`.release-build/update-51-settings.png`，采用隔离测试数据和显式微软雅黑字体做 offscreen 排版检查。

## 尚未验证与交付边界

- 未对真实生产素材库执行更新、恢复或移动操作。
- 未实际切断电源；断电后的恢复策略由会话阶段、数据门禁测试和操作文档覆盖，不声称真实断电实验通过。
- 未从已经公开的两个支持更新版本完成真实 GitHub 下载到安装的全链路：目前历史 Release 没有签名清单。
  已分别验证真实 GitHub 检查、Qt 下载与失败边界、离线签名导入、实际安装和启动成功核对。
- 未在所有 Windows 权限、磁盘和杀毒软件组合验证；Windows Authenticode 证书未配置，SmartScreen 仍可能提示。
- 未创建标签或公开 Release；首个支持更新的版本需要手动安装，后续按签名发布流程接入。
- 用户确认后才能 commit，之后再进行 PR、非作者审阅及发布授权流程。

## 0.0.1-rc2 评审后最终验证（2026-11-07）

用户暂定版本为 0.0.1-rc2，替代上述未公开的 0.0.2-rc1 候选；旧产物保留，不作为本轮交付。
审查与四项修复见 `review-rc2.md`。

- 更新专用测试：45 passed，4.62 秒。
- 完整回归：460 passed、1 failed，80.22 秒；唯一失败仍为既有启动升级 GUI 测试 `test_startup_upgrade_dialog_confirms_and_reports_progress` 等待超时。
- compileall、git diff --check：通过。
- 最终目录包：`.release-build/rc2-reviewed-dist/VideoOrganizer`；Qt DLL 检查通过。
- 包审计：1084 个文件，无个人设置、标签配置、数据库、视频或私钥；允许 certifi 公共 CA 证书。
- 正常安装包及离线签名资产：`.release-build/rc2-reviewed-delivery/`。
- 安装包：`VideoOrganizer-v0.0.1-rc2-setup-win-x64.exe`，104596266 字节。
- SHA-256：`16b3779581ee76cf954d99f2be94907a31219e1f7d34ab61970495a7a4ba2efd`。
- 使用仓库外既有 Ed25519 私钥生成清单，工具通过客户端公钥验签及大小/摘要复验；不公开发布。
- 最终隔离安装器：`.release-build/rc2-reviewed-smoke-installer/VideoOrganizer-v0.0.1-rc2-isolated-smoke.exe`，不得公开交付。
- 真实 EXE 隔离安装和启动：版本 0.0.1-rc2，更新会话 completed，WM_CLOSE 正常退出码 0。
- 再次覆盖安装：自选数据目录中 1 条合成记录和设置标记均保留，没有创建 application/data 空库。
- 证据：`.release-build/rc2-reviewed-installed-check/result.json`、`install-1.log`、`install-2.log`。
- 上述尚未验证边界继续有效；未经用户确认和批准，不 commit、push、打标签或发布。

## 0.0.1-rc2 候选筛选修复验证（2026-11-07）

- 用户授权修复子代理复审发现的 P2，范围见 spec.md“修复子代理复审发现的候选筛选问题”。
- 新测试先复现旧实现：2 failed、2 passed；失败是平台不适用时未请求后续候选。
- 修复后更新专用：`python -m pytest tests/test_software_update.py tests/test_software_update_gui.py -q`，50 passed，6.34 秒。
- 完整回归：`python -m pytest -q`，466 passed，110.50 秒。本次历史启动升级 GUI 测试通过；仍保留它此前偶发超时的记录，不能据此视为该历史问题已修复。
- compileall 与 git diff --check 通过。
- 重建使用新隔离目录 `.release-build/rc2-platform-fix-work` 和 `.release-build/rc2-platform-fix-dist`；不运行 build.py，不删除历史产物。
- 本节后续追加最终安装包及隔离安装验证证据；此前 rc2-reviewed-delivery 不含本次筛选修复。
- 最新交付目录：`.release-build/rc2-platform-fix-delivery/`，包含正常安装器、update.json、update.json.sig 和 SHA-256 文件；生成时已使用客户端公钥验签及复验大小/摘要。
- 安装器 `VideoOrganizer-v0.0.1-rc2-setup-win-x64.exe`：104592129 字节，SHA-256 `56956e0410f3136c9e4bdc4ae63d207d2372be76be52164e13c50958ad746124`。
- 最终目录包 Qt DLL 检查通过；1084 个文件审计未发现个人设置、数据库、素材或私钥。
- 新包真实隔离安装、启动核对及覆盖安装通过：版本 0.0.1-rc2，会话 completed，正常退出码 0，自选数据目录 1 条合成记录与设置标记均保留，无新空库。
- 证据：`.release-build/rc2-platform-fix-installed-check/result.json`、install-1.log、install-2.log。
- 仍未验证交互安装后默认重启的互斥时序，本次未修改该路径；静默冒烟不作为此路径通过证据。
- 未 commit、push、创建标签或公开 Release，待用户验收批准。
