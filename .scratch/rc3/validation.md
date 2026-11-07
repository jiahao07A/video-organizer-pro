# rc3 验证与评审记录

Status: claimed
Issues: #54 / #55
基线：6603ff948dab37484e4b3eaae0e12f16644c5fb4

## 自动验证

- 分析网关、镜像与已有更新专项：97 passed，6.63 秒。
- `python -m pytest -q`：488 passed，132.55 秒。
- `python -m compileall -q core gui tests`、`git diff --check` 通过。
- 镜像测试先以旧实现运行：7 failed、8 passed（缺少兜底接口/行为）；实现后通过。新增取消、响应超限、恶意重定向、普通403不兜底、明确限流403可兜底、官方成功不触镜像、部分内容重下等测试。
- AI错误提示回归先失败，修复后通过；明确不自动重试、不改图、无图像base64输出。

## 实际诊断与网络

- 原素材全546帧解码成功，1280×720、25fps；8张抽帧JPEG/base64有效，新旧安装模块/依赖和配置比对见 diagnosis.md。
- 同素材真实已配置AI服务共8次无缓存请求成功；没有复现原始400。隔离副本和探针留在 `.release-build/`，没有修改生产库或源素材，也不会提交私钥/配置/探针输出。
- 2026-11-07 用户确认当前rc2强制重新分析“现在可以成功”；不再稳定复现原400，不能宣称根因已修复。
- gh-proxy.org 实际可返回官方固定仓库公开 releases API JSON及rc2清单/签名。
- `.release-build/rc3-network-probe.py`：用本地503注入官方故障，随后真实Qt通过固定镜像访问发布列表、update.json、签名，内置公钥验签通过。不是仅使用urllib或只测伪造响应。
- 不承诺第三方镜像长期可用或无缓存滞后；镜像不能绕过签名/摘要。

## 子代理复审

- 按用户授权启用code-review双轴只读复审，固定基线及全部未跟踪源码，不让子代理修改或接触真实数据/凭据。
- 规范/安全轴：无确定发现；独立 `python -m pytest tests/test_update_mirror.py tests/test_ai_gateway.py -q`，47 passed，diff --check通过。
- 方案轴：无确定、可操作的新增缺陷；独立专项47 passed。明确原始400未复现，不能描述为根因已修复；没有独立验证Windows安装升级。
- 主Agent负责复核结果，不把原始400未复现写成已修复根因。

## 构建与交付

- 在 `.release-build/rc3-work`、`.release-build/rc3-dist` 独立构建，不使用build.py或清理旧目录。
- PyInstaller独立构建完成；Qt Core/Gui/Widgets DLL检查通过。包内敏感文件名审计仅发现certifi公共CA证书，未发现用户配置、数据库、视频或签名私钥。
- 正常与隔离冒烟安装器均编译成功。用户确认正常关闭rc2后，检查没有运行中的VideoOrganizer进程，再执行隔离安装验证；未强制结束进程。
- `.release-build/rc3-delivery/` 已生成正常安装包、update.json、update.json.sig及SHA-256文件。生成工具已使用客户端内置公钥验签，并复验安装包大小和摘要；目录全新，无覆盖已发布资产。
- `python tools/smoke_installer_update.py --installer .release-build/rc3-smoke-installer/VideoOrganizer-v0.0.1-rc3-isolated-smoke.exe --output .release-build/rc3-installed-check`：通过。真实隔离安装、启动退出码0、更新会话completed、自选数据目录及1条合成记录保留、第二次覆盖安装设置保留、未创建空库；证据见该目录result.json及两次安装日志。
- 安装包104598528字节，SHA-256：579ed52f85404f70a2877b8f1c96d60153efb5e12c79ac4b9f7ab38e011195d9。
- 本轮隔离测试不替代真实旧版到新版在线更新、交互默认重启时序或断电验收；上述既有限制仍保留。
- 2026-11-07 用户明确批准提交、合并与发布release；继续通过PR合并及草稿资产回验后公开的流程，不复用覆盖旧标签/资产。
