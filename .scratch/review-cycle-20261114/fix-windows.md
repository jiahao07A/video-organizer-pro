# Windows 构建问题修复执行记录

Status: 修复完成，等待独立复审与主 Agent 冻结包重建
执行角色：原 GUI 审查代理交叉执行 Windows 修复

## 按执行包完成的修改

- `installer/VideoOrganizer.iss`：在 `[Setup]` 设置 `MinVersion=10.0.17763`，阻止低于 Qt 6.10 支持边界的系统安装。
- `build_app.spec`：移除 `scipy` 排除，明确加入 `scipy.fftpack` 隐式导入，让已有 SciPy PyInstaller hooks 收集感知哈希所需 DCT 依赖；没有替换算法或修改核心逻辑。
- `README.md`：明确当前源码/安装器的 Windows 10 1809+、Windows 11 x64 支持范围，以及本轮 Windows 10 未取得实机/VM 验证。保留历史发布和验证数字不变。
- `tests/test_review_windows_bundle.py`：验证安装器最低系统契约、构建配置确实包含且不排除 SciPy、用真实 `imagehash.phash` 及 `VideoProcessor.calculate_phash` 对合成图执行 DCT 并核对结果非空且一致。

## 验证

```powershell
rtk proxy python -X utf8 -m pytest tests/test_build.py tests/test_verify_windows_qt_bundle.py tests/test_review_windows_bundle.py -q -rs
```

结果：8 passed in 1.19s，无跳过。

`rtk git diff --check` 通过。独立导入 `scipy.fftpack` 成功，新测试 AST 解析成功。

## 边界

本代理未重建冻结包、未编译或运行安装器，因此新测试只能证明源码行为和打包配置契约，不能证明新冻结包运行通过。主 Agent 将重建并检查实际收集结果。未取得 Windows 10 实机验证，README 已明确。未安装依赖、未提交、未修改执行包外产品文件、未删除目录或读取真实素材/凭据。

## 下一步

由原 Windows 审查代理独立复审本次修改；主 Agent 重建后核实 SciPy 模块/DLL 包含及 Qt 启动/安装流程。
