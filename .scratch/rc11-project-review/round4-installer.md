# 第四轮：真实编译暴露的安装器语法回归

Status: claimed

负责修复：desktop。负责独立复验：platform。主审已确认当前差异把两行 `; Inno...` 说明放在 [Code] Pascal 段之后、[UninstallDelete] 之前。Inno 将其作为 Pascal 内容解析，真实 ISCC 构建退出2，110行报 BEGIN expected。此前文本测试未捕获这种语法错误。

明确方案：仅把这两行中文说明移动到 [UninstallDelete] 标题下一行，继续保留单文件删除安装标记，不引入 filesandordirs。不得改安装标识、版本、发布参数或其他代码。修复后以真实 ISCC 编译 isolated-smoke 安装器作为验收，不能只做字符串断言。禁止提交。

desktop 只修改 installer/VideoOrganizer.iss，上报差异后由主审确认并通知 platform。platform 用已冻结且构建成功的 EXE，沿用 final-platform.md 的流程，将安装器输出改为全新的 .release-build/review-rc11-final-installer-b。主审会更新 installer 源码校验和，GUI/Python 未发生变化。

全程 pwsh.exe + rtk，不批量删除、不操作正式安装。不新增代理。
