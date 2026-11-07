状态更新：更新机制实现及本地验证完成，等待用户人工验收和 commit 授权。

- 分支：feat/51-software-update；本地规格：.scratch/software-update/spec.md；验证记录：.scratch/software-update/validation.md。
- 新增后台检查、正式/测试渠道、下载与离线导入、Ed25519 签名和 SHA-256 校验、安全退出安装、数据备份及启动核对。
- 更新专用测试 41 passed；完整套件 452 passed / 1 failed，失败为既有首次升级 GUI 对话框等待超时，未掩盖该限制。
- PyInstaller 与简体中文 Inno Setup 安装器构建通过；真实 EXE 隔离安装、原自选数据目录启动核对、正常退出与二次覆盖安装通过。
- 包内隐私审计通过；签名私钥保存在仓库外，仅公钥进入客户端。
- 未操作生产素材库，未做真实断电试验，尚无两个公开签名版本用于线上全链路升级验证。
- 未 commit、推送、创建 PR 或公开发布。按 AGENTS.md 等待用户确认无 bug 并批准提交后继续。
