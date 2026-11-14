# Windows 修复独立二次审查

Status: 审查通过；冻结包与 Win10 实机证据仍待补充
审查者：R1-F 原 Windows 审查代理
修复者：R1-C 原 GUI 审查代理（交叉执行）

## 结论

读取 `fix-windows.md` 和实际 diff，对照 R1 的 WIN-01、BUILD-02 复审，未发现此次修复中的可确认回归。

- `[Setup] MinVersion=10.0.17763` 与当前 Qt 6.10 官方最低 Windows 10 1809 对齐，未改变架构、安装权限、互斥量、用户数据保留与更新参数。
- `scipy` 已从排除项移除，并把实际由 ImageHash 延迟导入的 `scipy.fftpack` 加入隐式导入。保留成熟库与原计算入口，没有替换 DCT/感知哈希算法。
- README 明确当前支持的最低系统及本轮缺少 Win10 实机证据，没有把本机 Win11 结果写成 Win10 验证通过。
- 三个新测试分别验证安装器契约、构建配置依赖、真实合成帧经 `imagehash.phash` 和产品入口的一致性。没有调用 mock 哈希或用非负/可空断言伪造通过。

## 独立验证

```powershell
rtk proxy python -X utf8 -m pytest tests/test_build.py tests/test_verify_windows_qt_bundle.py tests/test_review_windows_bundle.py -q -rs
rtk git diff --check
```

结果：**8 passed in 1.45s**，无跳过；空白检查通过。

## 仍需主 Agent 完成

源码及 AST 配置测试无法证明 PyInstaller 最终收集 SciPy/FFT 的模块与二进制依赖。须重建冻结包并验证实际感知哈希入口/Qt 启动；重新编译安装器确认最低系统指令接受。Windows 10 1809+ 实机稳定性仍缺环境，不因上述审查通过而自动补记。未修改产品、测试、文档或提交。
