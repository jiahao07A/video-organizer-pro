# 实施完成，待人工验收

关联：#63；分支：`feat/63-http400-recovery`；本地规格：`.scratch/http400-recovery/spec.md`。

已实现视频400持久标记、自动恢复、单条与全筛选范围批量手动恢复、三种手动模式、全局/任务/视频/本次逐字段策略、有序多API兜底和实际请求预算。恢复等待释放分析工作线程；取消、鉴权失败、重启中断、旧成功结果保留与跨供应商缓存隔离均纳入处理。数据库升级到版本2，升级前备份；最终结果、解除标记、标签计数与待审词写入具有事务一致性。

2026-11-08最终自动验证：

- `rtk proxy python -m compileall -q core gui`：通过。
- `rtk proxy python -m pytest -q`：**555 passed、9 skipped、25 warnings，94.92秒**。
- `rtk git diff --check`：通过。

测试使用模拟API、可控调度及临时SQLite；覆盖请求次数/预算、兼容重发、兜底顺序、主API成功/兜底成功/全部失败、排除、取消、配置继承、手动模式、完整分页目标、并发隔离、重启恢复、迁移备份与事务回滚、缓存隔离及成功副作用。Qt离屏测试覆盖配置与界面数据，已目视核对设置布局。25条警告来自现有筛选模型的Qt弃用接口；9项为环境条件下跳过。

使用说明、设置入口与人工清单：`docs/http400-recovery.md`。架构决策：`docs/adr/0011-http400-video-recovery.md`。

尚未以真实供应商、真实素材或安装包完成人工验收。按用户约定，待用户确认无bug并明确批准后再commit；当前未commit、push或创建PR，Issue保持开放。

## 0.0.1-rc5本地打包验证（2026-11-08）

用户要求打包0.0.1rc5，统一版本源更新为`0.0.1-rc5`。版本/更新专项回归32项通过；此前555项全量结果仍适用于功能实现，打包阶段仅修改版本号与交付记录。

- PyInstaller独立workpath/distpath构建通过；包体审计1084个文件、365944418字节，恢复模块和rc5版本均在归档内，无用户配置、数据库、密钥、日志或素材。Qt动态库加载通过。
- 标准安装包：`.release-build/rc5-http400-installer/VideoOrganizer-v0.0.1-rc5-setup-win-x64.exe`，104661551字节。
- SHA-256：`f913d525ebd20475950b20b4e006b3bccb2da0e78de267cef28cfcfc5a55b37b`；校验文件同目录`SHA256SUMS.txt`。
- 同目录包构建隔离安装器，执行`tools/smoke_installer_update.py`通过：版本rc5、启动退出码0、更新会话completed、自选数据和1条合成素材保留、二次覆盖安装设置保留。结果见`.release-build/rc5-http400-installed-check/result.json`。

安装/启动冒烟已验证；真实API与400恢复界面仍待用户按使用说明验收。未公开发布、未commit。

## 用户反馈后的设置统一与最终rc5包

详见`settings-unification.md`：普通重试和400恢复统一在主页面“分析失败处理”，按范围切换覆盖并通过“保存所有设置”持久化；常用项直接显示，高级项折叠，继承与原配置格式保持兼容。

最终全量560 passed、9 skipped、25 warnings，176.50秒；设置专项27项通过。截图核对后修正了高级表格的按钮重叠，新增窄窗口回归。最终目录包、Qt库、隔离安装和二次覆盖安装保留数据均通过。

最终交付（优先使用此包）：`.release-build/rc5-settings-final-installer/VideoOrganizer-v0.0.1-rc5-setup-win-x64.exe`，104679402字节，SHA-256 `66244c6f794a1c3a79a27a9a7c70d633b86024d18b55df74f9d6dddd4e004eb3`。此前安装包保留，未覆盖；未commit、未公开发布。

## 模型列表功能最终验证与rc5交付（2026-11-08）

- 共用模型选择器已覆盖档案默认模型、任务路由、400备用API及手动指定备用；支持获取、搜索选择与任意手填。获取不会自动替换输入或保存配置。
- 专项71项通过；完整回归577 passed、9 skipped、25 warnings（现有Qt弃用提示），104.43秒；compileall与git diff --check通过。
- 合成配置截图已检查，使用模拟API与临时数据库，没有调用真实供应商或操作真实素材。真实供应商模型列表及适用性待用户验收。
- PyInstaller构建、包体内容与Qt依赖检查通过；新版模块和0.0.1-rc5版本已核验，不含个人配置、数据库或素材。
- 隔离安装及覆盖更新通过：启动退出码0、更新会话completed、自选数据和1条合成记录保留、二次覆盖安装设置保留。
- 最新交付包（替代前文旧包作为本轮首选）：`.release-build/rc5-model-selection-installer/VideoOrganizer-v0.0.1-rc5-setup-win-x64.exe`。
- 大小：104671633字节；SHA-256：`3aaab85b429256eb87dacc6e903288bad5fd7e0b173a4695b91d8f8760128abf`，同目录保存校验文件。
- 操作说明：`docs/模型列表与选择.md`。旧包保留；尚未commit、push或公开发布。待用户确认无bug并批准后再commit。
