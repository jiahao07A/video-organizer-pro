# 设置中心与配置兼容最终安装器

Status: completed
GitHub Issue: #85
Branch: feat/85-settings-center-redesign
Source commit: 08a02217465458a5b8ce1cdb10b24441cf7e021a

## 范围与约束
用户要求打包并构建安装器。基于已提交的设置中心和配置兼容代码，保持 APP_VERSION=0.0.1-rc10，使用全新 `.release-build/package-20261121-settings-config/`。复用既有 build.py、PyInstaller spec、Inno Setup 和隔离安装冒烟工具。旧构建、scratch、真实用户配置和素材全部保留。无业务源码修改，不公开发布，不执行生产覆盖安装，不自动提交或使用subagent。

## 验证与风险
检查冻结模块与已提交源码一致、Qt核心DLL可加载、无用户配置或数据库污染；编译正式及隔离测试安装器，隔离双安装并验证正式启动退出及数据保留。额外用冻结程序验证合成旧配置自动升级及源字节备份。记录版本、提交、文件摘要及安装结果。当前未提供Windows签名，本轮不改变签名策略。

## 任务清单
- [x] 核对提交、版本、打包规则、工具和新输出范围。
- [x] 执行配置兼容与打包专项回归（72 passed，4.31s）。
- [x] 构建目录包，核对冻结源码、Qt依赖和数据污染。
- [x] 编译正式及隔离验证安装器（两次编译均退出0）。
- [x] 隔离安装、启动、重复安装及配置升级验证。
- [x] 生成SHA256、安装说明与验证记录，交付路径。

## Comments
前轮业务/GUI回归371项通过；配置兼容与模型最终55项通过。两个已提交变更分别为32fc3b3（配置兼容）与08a0221（设置中心）。本轮以新安装器包含二者为验收目标。

### 目录包验证
PyInstaller 6.19.0 / Python 3.13.12构建退出0，179秒。目录包1253文件、608090041字节；15个关键模块在工作PYZ和exe内嵌PYZ中均与源码字节码一致，新增配置模块与packaging.version均包含。版本0.0.1-rc10、PE x64，Qt6Core/Gui/Widgets加载通过，无用户配置、定位文件、备份、数据库或日志污染。额外凭据扫描最初将certifi/cacert.pem列为可疑，核对它仅为依赖的公共根证书且无PRIVATE KEY后添加精确路径白名单，复核通过。正式与隔离Inno安装器编译中。

### 安装器编译
正式与隔离测试安装器编译均退出0。正式安装器Windows签名检查NotSigned；旧settings-reviewed安装器SHA256仍为d8dce44f7ce45e74af17cdd4d93963f905fac22b871a0b021c2200dbcbcd2e91，确认未覆盖。新包正在使用既有隔离双安装冒烟工具验证。

### 冻结程序重复启动检查的观测
隔离双安装成功，启动退出0、设置及1条记录保留。新增冻结旧配置升级检查首次运行已通过所有值/备份断言，但“整个GUI启动不改变任何配置mtime”断言失败。追加只观察当前配置的第三次启动确认：settings字节完全相同，仅mtime变化；tag_config字节及mtime均不变；升级快照数不变。源码核对发现既有WorkstationPage._apply_column_preferences将带_schema的raw与剔除_schema的prefs比较，导致等值保存。此既有界面行为不属于配置加载器再次升级，本轮不修改已提交业务源码。将冻结验证明确限定为内容/升级备份不变，保留tag_config的mtime严格检查，并在交付记录公开settings等值保存的观测，不声称整个GUI无磁盘写入。

### 最终交付与验证
- 正式安装器：`.release-build/package-20261121-settings-config/installer/VideoOrganizer-v0.0.1-rc10-setup-win-x64.exe`，166628728字节。
- SHA256：`7dffa2ab2c1254f86b948e895c9522c3d3566ba10c5e62c3239d0d94bf21d0ab`；同目录包含`.exe.sha256`及`安装说明.md`。
- 详细机器可读记录：输出根目录`verified-release.json`，以及`verified-bundle.json`、`verified-installed.json`和`installation-smoke/result.json`。
- 隔离双安装、更新核对completed、正式启动退出0、1条记录与设置保留通过；1253安装文件与目录包逐个SHA256一致。
- 冻结程序验证旧API、显式空模型、BOM标签旧结构、全局Prompt、导出、监控身份/禁用/覆盖和扩展字段保留，原始源字节备份一致；两次启动退出0，配置内容与升级快照不变。
- Windows Authenticode未签名。未生产覆盖安装、未公开发布、未改变版本号或业务源码，旧构建已核对哈希保留。Git已跟踪文件与暂存区均无改动。
