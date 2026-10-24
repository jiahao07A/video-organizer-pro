# Video Organizer Pro

> 面向视频后期工作流的 Windows 桌面素材整理工具：用 AI 批量分析视频内容，统一管理标签与摘要，预览整理方案，再由用户确认文件操作。

[![最新版本](https://img.shields.io/github/v/release/jiahao07A/video-organizer-pro?display_name=tag&sort=semver)](https://github.com/jiahao07A/video-organizer-pro/releases)
[![Windows x64](https://img.shields.io/badge/platform-Windows%20x64-0078D4)](https://github.com/jiahao07A/video-organizer-pro/releases/tag/v0.0.0)

## 下载正式版

当前正式版为 **v0.0.0**，提供 Windows x64 标准安装程序：

- [下载 `VideoOrganizer-v0.0.0-setup-win-x64.exe`](https://github.com/jiahao07A/video-organizer-pro/releases/download/v0.0.0/VideoOrganizer-v0.0.0-setup-win-x64.exe)
- [查看所有版本与校验值](https://github.com/jiahao07A/video-organizer-pro/releases)

安装程序提供标准 Windows 安装向导、开始菜单入口、可选桌面快捷方式和卸载入口。安装版不需要用户预先安装 Python。首次启动时，程序会在安装目录旁建立 `data` 管理数据目录；如果安装位置不可写，会提示选择其他目录。

> AI 分析仍需要用户配置可用的 OpenAI 兼容 API。`ffmpeg` 仅在音频转录或视频内部元数据处理等功能需要时使用，不随本安装程序承诺内置。

## 这个项目解决什么问题

视频素材的文件名通常不能表达画面内容。后期整理时，用户需要重复浏览、分类、贴标签、写摘要、重命名、移动目录，并把元数据交给剪辑软件。

Video Organizer Pro 将这些工作放在一个桌面工作台中：

1. 设定当前要处理的**工作范围**。
2. 扫描并登记视频素材，生成缩略图。
3. 通过 AI 批量生成分类、标签和摘要。
4. 使用搜索和高级筛选定位素材。
5. 先模拟预览重命名或物理整理方案。
6. 确认后执行文件操作，并查看逐项结果。
7. 导出 FCPX XML、ALE 或 XMP 侧车供后期软件使用。

“AI 自动整理”表示分析和方案生成可以批量完成，不表示程序会绕过用户确认直接修改原始文件。

## 当前能力

### 视频工作台与素材库

- **工作台**：只显示当前工作范围内的素材，分析和整理操作不会越过工作范围。
- **素材库**：浏览全部已入库视频，可搜索、筛选并将目标素材累加到工作范围。
- 支持列表视图和卡片视图、应用内素材预览、缩略图、详情编辑、列显示和列宽记忆。
- 支持按列表选中执行批量操作；没有列表选中时，操作目标集为当前可见列表全部内容。
- 支持文件或文件夹作为工作范围，支持“浏览”替换范围和“累加”追加范围。
- 支持 `.mp4`、`.mov`、`.avi`、`.mkv`、`.m4v`、`.flv`、`.wmv`。

### AI 分析

- 抽取视频帧并调用 OpenAI 兼容接口生成分类、摘要和标签。
- 可选场景检测、音频转录和缩略图生成。
- 支持最多 5 个模型供应商档案，每个 AI 功能可以单独配置供应商和模型。
- 当前任务模型路由包含：视频分类、标签生成、内容描述、待审词 AI、近义巡检、标准词 AI 助手。
- 对瞬时请求错误、单条视频失败和首轮失败集合提供分层重试；分析界面显示总进度与本轮进度。
- 已分析素材默认跳过；勾选“强制重新分析”后才会覆盖分类、摘要和标签。
- 分析失败不会被伪装成成功；取消分析后，已成功结果保留，未开始的项目不会继续进入补跑。

### 标签库

- 标签按标签组管理，支持标准词、别名和待审词。
- 封闭组只接受标签库中的标准词；无法归一的词进入待审审核，不会被模糊贴到“最相近”的词。
- 待审词可以批准为指定标签组的新标准词、挂到已有标准词下作为别名，或丢弃。
- AI 只提供待审词建议、近义巡检建议和标准词助手建议；应用建议前必须由用户明确确认。
- 支持词表导入、标签组维护、导出规则和标签使用统计。

### 文件整理与导出

- 模拟重命名：展示原文件名和目标文件名，不执行物理改名。
- 应用重命名：确认后执行重命名，目标冲突时使用不覆盖策略并报告结果。
- 重命名回滚：提供上一批重命名的恢复入口。
- 物理整理：先显示源路径、目标路径、冲突和风险，再逐项确认后移动文件。
- 导出 FCPX XML、ALE（适用于 DaVinci Resolve 的元数据导入）和 XMP 侧车。
- 支持管理数据备份、恢复和数据目录选择/搬迁。

## 安装与首次使用

### 1. 安装

1. 从 [Releases](https://github.com/jiahao07A/video-organizer-pro/releases) 下载 `VideoOrganizer-v0.0.0-setup-win-x64.exe`。
2. 运行安装程序，按向导选择安装目录。
3. 可选创建桌面快捷方式；开始菜单入口会自动创建。
4. 从开始菜单或桌面启动 **Video Organizer Pro**。

安装程序只安装应用文件。它不会预置个人设置、API Key、数据库、标签配置、日志或视频素材。

### 2. 配置 AI 服务

打开“系统设置 → AI 引擎”：

1. 新建或编辑一个模型供应商档案。
2. 填写 API Key 和 API Base URL。
3. 填写档案默认模型名，或在“任务模型路由”中为不同 AI 功能分别指定模型。
4. 保存设置后再开始分析。

API Key 只保存在本机管理数据目录中。不要把 `settings.json`、截图、日志或导出的配置提交到 GitHub。

项目使用 OpenAI 兼容调用方式；具体服务需要支持项目使用的聊天补全、视觉输入和 JSON 输出能力。模型名称、价格、上下文长度和服务可用性由用户使用的 API 服务决定。

### 3. 设定工作范围并分析

1. 进入“工作台”，点击“浏览”选择视频文件或文件夹。
2. 需要追加其他路径时点击“累加”；再次点击“浏览”会替换当前工作范围。
3. 确认列表中只有本次要处理的素材。
4. 点击“开始分析”。已分析素材默认跳过，失败或未分析素材会进入分析目标集。
5. 在列表、卡片和详情面板中检查分类、标签、摘要和预览。

### 4. 预览并执行整理

建议使用以下顺序：

1. 先点击“模拟重命名”，检查原文件名与目标文件名。
2. 对移动目录使用“模拟预览 — 物理整理”，检查冲突和风险。
3. 使用列表选中或筛选结果缩小操作目标集。
4. 确认后再点击“应用重命名”或执行物理整理。
5. 对需要交给剪辑软件的素材，另行导出 FCPX、ALE 或 XMP。

不要在唯一原始素材上首次测试移动、重命名或视频内部元数据写入。先复制少量测试素材，并保留原文件备份。

## 数据目录

安装版默认使用程序旁的 `data` 目录。管理数据包括：

| 内容 | 作用 |
| --- | --- |
| `video_organizer.db` | 素材库、标准词、别名、待审词和历史记录的主要数据源 |
| `settings.json` | API Key、模型路由、界面偏好和处理设置 |
| `tag_config.json` | 标签组规则、Prompt 和导出方案配置 |
| `.thumbnails/` | 缩略图缓存 |
| `backups/` | 管理数据备份 |
| `video_analysis_results.json` / `.csv` | 用户主动导出的分析结果副本 |

可以在“系统设置 → 管理数据目录”中选择或搬迁管理数据。搬迁前会备份、复制并验证，原目录会保留；原始视频文件路径不会因管理数据目录搬迁而改变。

卸载程序时，应用管理数据通常会保留在原目录中。重新安装后可以继续使用该目录；如果要彻底移除本地数据，需要用户自行确认后删除备份、数据库和设置文件。

## 重要边界与风险

- **AI 依赖外部服务**：没有有效 API Key、Base URL、模型或网络时，分析无法保证成功。
- **默认不自动写 XMP**：分析结果会写入管理数据；“分析成功后自动同步 XMP”默认关闭。开启后会为成功分析的视频写入同名 `.xmp` 侧车，已有同名侧车可能被覆盖。
- **物理整理会改变文件位置**：确认前会展示方案，但移动文件仍可能受到权限、占用、跨磁盘和目标冲突影响。
- **视频内部元数据写入风险较高**：启用相关设置前必须保留原视频快照；程序不承诺没有原文件备份时可以完整恢复。
- **回滚范围有限**：当前提供上一批重命名回滚入口；这不等于移动、XMP、视频内部元数据和所有数据库变化都能一键完整回滚。
- **数据目录不是视频备份**：管理数据备份不包含原始视频文件。
- **安装目录权限**：如果程序目录不可写，首次启动会要求另选可写的管理数据目录，不会静默创建一个新的空库掩盖原数据。

## 从源码运行

正式版用户优先使用安装程序。开发者可以使用 Python 3.11 或 3.12 建立虚拟环境运行源码；当前发布构建已在 Windows x64、Python 3.13.12 环境验证过，但源码依赖尚未锁定到 `requirements.txt` 或 `pyproject.toml`。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install PySide6 qt-material opencv-python numpy openai Pillow ImageHash "scenedetect[opencv]" pytest
python video_organizer_pyside6.py
```

也可以直接使用：

```powershell
python video_organizer_app.py
```

若需要音频转录或视频内部元数据处理，请另外安装可从命令行调用的 `ffmpeg`，并在设置中启用对应功能。

## 开发者构建

仓库使用 PyInstaller 的 `build_app.spec` 生成 Windows 目录包。`build.py` 会清理当前目录下的 `build/` 和 `dist/`，因此发布构建应使用隔离的工作路径和输出路径：

```powershell
python -m pip install PyInstaller
python -m PyInstaller build_app.spec --noconfirm `
  --workpath .release-build\work `
  --distpath .release-build\dist
```

输出目录为：

```text
.release-build\dist\VideoOrganizer\VideoOrganizer.exe
```

正式版安装器的 Inno Setup 配置位于 `installer/VideoOrganizer.iss`。安装器工具只用于维护者构建，不需要随用户安装。

## 验证记录

`v0.0.0` 正式版发布前执行了以下检查：

- 自动化测试：`python -m pytest -q -k "not test_startup_upgrade_dialog_confirms_and_reports_progress"`
- Python 编译检查：`python -m compileall -q core gui tests video_organizer_app.py video_organizer_pyside6.py`
- Git 空白检查：`git diff --check`
- PyInstaller Windows x64 目录包构建
- 发布包清单审计：排除 API Key、设置、标签配置、数据库、日志、备份、缩略图缓存和真实视频
- 隔离安装后的启动/退出与标准卸载流程冒烟

已发布资产页面会记录实际测试数量、Python/PyInstaller/Qt 版本、安装包大小和 SHA-256。自动化测试通过不代表外部 AI 服务、所有视频编码、剪辑软件导入和所有文件系统权限组合都已覆盖。

## 项目结构

```text
video-organizer-pro/
├── video_organizer_app.py       # PySide6 应用入口
├── video_organizer_pyside6.py   # GUI 启动包装
├── video_organizer.py           # CLI 兼容入口
├── core/                        # 素材库、分析、标签、数据目录和导出服务
├── gui/                         # 工作台、素材库、标签库和系统设置
├── tests/                       # 自动化测试
├── installer/                   # Windows 安装器配置
├── build_app.spec               # PyInstaller 配置
├── CONTEXT.md                   # 产品领域词汇
└── docs/                        # ADR 与协作规则
```

## 常见问题

### 安装版需要 Python 吗？

不需要。GitHub Release 中的 Windows x64 安装程序已经包含运行所需的 Python 和应用依赖。

### 安装版是否完全离线？

程序本身可以离线启动和浏览已有素材，但 AI 分析需要可用的外部 API。启用音频转录或视频内部元数据处理时还需要 `ffmpeg`。

### “取消入库”和“移出工作范围”有什么区别？

“移出工作范围”只改变工作台当前处理边界，素材库记录和磁盘文件保留；“取消入库”移除素材库管理记录，默认不删除磁盘视频文件。

### 没有选中任何列表项时会处理什么？

批量操作会处理当前可见列表全部内容；工作台仍会限制在当前工作范围内。确认对话框会显示实际条数和操作语义。

### 可以把标签 AI 建议直接写入标签库吗？

不能静默写入。待审词 AI、批量分拣、近义巡检和标准词 AI 助手只生成建议，用户明确确认后才会写入标准词、别名或标签组变更。

### 反馈问题时应该提供什么？

请说明应用版本、Windows 版本、复现步骤、是否使用安装版、相关设置是否启用，并附脱敏后的错误信息。不要上传 API Key、真实视频、数据库或包含个人路径的完整日志。

## 相关文档

- [项目领域词汇](./CONTEXT.md)
- [架构决策记录](./docs/adr/)
- [本地议题追踪约定](./docs/agents/issue-tracker.md)
- [Git 与 GitHub 工作流](./docs/agents/git-github-workflow.md)

## 许可证

当前仓库未在根目录声明开源许可证。使用、再分发或二次开发前，请先与项目维护者确认授权范围。
