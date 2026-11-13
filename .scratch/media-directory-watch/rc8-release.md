# RC8 提交、合并与发布

Status: resolved
GitHub Issue: #72
Branch: feat/72-media-watch
Version: 0.0.1-rc8

用户于 2026-11-13 明确授权 commit、合并和公开 Release。提交当前已完成七轮独立评审的产品源码、测试、依赖、ADR 及必要规格；临时脚本、截图、测试数据库、原始素材和构建产物保持本地。通过 PR rebase 合并，从合并后的源码在全新目录重建，生成 Ed25519 签名清单并验证隔离安装、下载回验后公开预发布。

## 任务清单

- [x] 核对用户授权、工作流、现有改动及远程基线。
- [x] 使用构建 Python 执行最新完整测试：777 passed、9 skipped、25 条既有 Qt 弃用警告；44 个文件的暂存范围无素材或二进制产物，compileall 与 diff --check 通过。
- [x] 提交 66052f9836d1b6fbf89bad6b59e71480d2884f2f，PR #74 已 rebase 合并；主分支提交 4cd839541fa445a1bda161934d2cdc597588a7b2。独立评审证据、用户授权和验证结果已记录到 PR 与 Issue。
- [x] 从合并后源码重建：78 个项目模块及启动入口与源码一致；1084 个文件中无用户配置、数据库、素材和私钥；Qt DLL 加载通过。
- [x] 隔离安装、1084 文件一致性、后台启动和正常退出通过，登录启动项未变；更新完成核对及二次覆盖安装保留测试设置与合成记录。
- [x] Ed25519 签名、草稿上传、下载回验的签名/大小/SHA-256 全部通过；v0.0.1-rc8 已于 2026-11-13 公开为预发布。
- [x] 更新 Issue、PR 与本地发布证据。

## 风险与回滚

数据库升级到版本 5，升级前须按既有协议备份；回退旧版前恢复升级前管理数据备份，不能直接让旧版写入新库。保留 RC7 和旧安装产物。真实付费 API、Windows 重新登录自启及真实显示器高 DPI 仍属人工边界，不以隔离测试替代。

## 验证证据

最终完整测试 777 passed、9 skipped（290.27 秒）；显式本地 HTTP 压力测试 9 passed（273.31 秒），合计 786 项通过。25 条警告为既有 Qt invalidateFilter 弃用警告。源码与合并结果无差异，未重复运行相同完整套件。

全新发布构建目录 `.release-build/rc8-release-20261113/`。使用 PyInstaller 6.19.0 / Python 3.13.12 构建，未运行旧清理脚本。包内源码、依赖与隐私边界证据为 package-audit.json。此前未提交临时脚本、旧截图、数据和产物均保留在本地，不参与本轮 commit。

安装及启动结果保存在 installer-result.json、startup-smoke/result.json、install-smoke/result.json。专用隔离安装器不写生产卸载项或快捷方式，后台启动不改用户登录启动配置；覆盖安装使用合成素材记录与独立管理目录。签名回验结果见 download-verification.json。

## 公开结果

- 产品 PR：https://github.com/jiahao07A/video-organizer-pro/pull/74
- 发布：https://github.com/jiahao07A/video-organizer-pro/releases/tag/v0.0.1-rc8
- 标签对应提交：4cd839541fa445a1bda161934d2cdc597588a7b2（已 fetch 回核对）。
- 安装包：VideoOrganizer-v0.0.1-rc8-setup-win-x64.exe，104892622 字节。
- SHA-256：`49fcde541d2da1d8da98a101c4a321b4e858346d0187d7c57e06ff0a28edc5cd`。
- 公开资产共4项：标准安装器、update.json、update.json.sig、安装器.sha256。专用隔离测试安装器不发布。
- GitHub 返回 isDraft=false、isPrerelease=true；保留 RC7 与所有旧资产，未将 RC8 标为稳定最新版。

正式发布使用合并后重新构建的上述安装包；此前手工测试包不作为本次公开资产。
