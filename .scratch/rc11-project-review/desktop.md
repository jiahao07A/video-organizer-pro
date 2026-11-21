# desktop 第二轮执行包

Status: claimed

实施任务：你负责修复 CLI 与导出及安装器。读取相邻 spec.md。video_organizer.py 抽出可测 main，在服务构建后冻结 scan_videos 指定绝对路径，分析与改名同目标，空目录和失败不得退回全库，--dry-run 输出逐项，--workers 写入实际 processing，错误退出码，支持文件或目录。core/video_organizer_service.py 限 ALE/显式结果导出方法，清洗每个 ALE 字段 CR LF TAB；恢复用户主动 JSON/CSV 导出，工作台 gui/views/workstation.py 现有导出菜单提供入口，保留选中/可见范围和副本门禁、选目的地、不得静默覆盖源素材，README 描述。installer/VideoOrganizer.iss 移除卸载递归清理 _internal，仅允许 Inno 清单卸载，保护用户文件。给专属回归包含真实临时 DB 和越界哨兵、带换行字段和用户导出结果；受控代码小区块修改，先红后绿。不要改其他包或提交。完成后保留可复核测试结果和 final。

全部命令使用 pwsh.exe 和 rtk；真实素材及用户配置禁止用于测试。收到空白派发通知时亦可按此文件实施；只有主审可以更新所有任务状态。
