# Codex 文件型记忆系统

一个轻量、可审计、可迁移的 Codex 文件型记忆系统模板。它把长期上下文保存为普通 Markdown 文件，不依赖数据库、不上传对话内容，适合个人开发者、学生和研究者在本机管理自己的记忆。

当前版本：`0.2.0`。许可证：[MIT](./LICENSE)。

## 你会得到什么

- `MEMORY.md` 热层入口：保存索引、进行中任务和可复用的错误模式；
- `spaces/` 分类记忆：按主题按需读取，减少上下文污染；
- `ARCHIVE/` 冷归档：按日期检索历史，不要求全量加载；
- Python CLI：跨 Windows、macOS 和 Linux，提供安装、检查和接入说明；
- 安全更新：只补入缺失模板，不覆盖已有记忆；更新失败时保留版本元数据备份；
- 只读检查：校验结构、大小、编码、日期、重复条目和公开仓库隐私风险；
- 可选 `PreToolUse` 安全钩子：拦截敏感凭据读取和高破坏性命令。

## 支持平台与依赖

- Windows 11 或更新版本；
- macOS 12 或更新版本；
- 常见 Linux 发行版；
- Python 3.11 或更新版本。

CI 会在 Windows、macOS、Ubuntu 以及 Python 3.11--3.14 上运行自动化检查；本地开发仍应以目标机器上的实际命令结果为准。

## 快速安装

先克隆仓库并进入目录：

```text
git clone https://github.com/shu0819-sjy/codex-memory-system.git
cd codex-memory-system
```

Windows PowerShell：

```powershell
./codex-memory.ps1 install --non-interactive
```

macOS / Linux：

```sh
sh ./codex-memory install --non-interactive
```

所有平台的通用入口：

```sh
python -m codex_memory install --non-interactive
```

默认安装到 `~/.codex-memory`（Windows 即 `%USERPROFILE%\\.codex-memory`）。需要指定位置时，把 `--root` 加到命令末尾：

```sh
python -m codex_memory install --non-interactive --root '/path/to/memory'
```

首次安装要求目标目录不存在。安装器只从公开的 `memory/` 模板复制文件，并写入 `.template-version`；不会读取或修改你的其他 Codex 配置。

## 检查、更新与回滚

检查已安装目录：

```sh
python -m codex_memory check
```

检查只读文件，不会修复或改写记忆。返回码为：`0` 表示通过或仅有警告，`1` 表示检查失败，`2` 表示参数或运行错误。

更新到当前模板版本：

```sh
python -m codex_memory install --update --non-interactive
```

更新只补入缺失文件并更新 `.template-version`，不会覆盖已有 `MEMORY.md`、分类文件或归档。更新前会尝试创建 `.template-version.backup-*`；更新失败时错误消息会给出备份位置。需要回滚时，先停止对该目录的写入，核对备份内容，再按操作系统使用文件管理器或 `Copy-Item` / `cp` 手工恢复版本元数据。项目没有自动删除或强制覆盖命令。

卸载不应直接执行递归删除。确认已经备份自己的记忆后，先用 `check` 检查目标，随后只删除安装目录；如果目录中混有自己的文件，请逐项清理，不要使用未经核对的通配符。

## 接入 Codex 工作流

把 [AGENTS.example.md](./AGENTS.example.md) 的相关约束合并到自己的 `AGENTS.md`，将 `<MEMORY_ROOT>` 换成实际安装路径。也可以让 CLI 生成不写磁盘的接入说明：

```sh
python -m codex_memory instructions
```

系统不会自动修改模型、权限、网络、中转配置或 `AGENTS.md`。记忆正文始终是本地数据；只有用户明确要求时，才按 `RULES.md` 写入记忆。

## 可选安全钩子

`hooks/pre_tool_guard.py` 是可选组件，不会自动修改你的 Hooks 配置。接入前请先备份现有配置，并根据当前 Codex 版本的 Hooks 文档注册 `PreToolUse`。

它只处理两类风险：读取或复制 `.env`、私钥、凭据等敏感文件；以及强制重置、批量删除、停止进程、关机等高破坏性命令。普通命令返回 `continue: true`，解析异常也放行，避免钩子故障阻塞工作流。

## 目录结构

```text
memory/
├─ MEMORY.md              # 热层入口
├─ RULES.md               # 写入规范
├─ config.json            # 检查阈值与分类配置
├─ spaces/                # 分类记忆模板
└─ ARCHIVE/               # 冷归档
hooks/
└─ pre_tool_guard.py      # 可选安全钩子
codex_memory/             # 跨平台 Python CLI
codex-memory.ps1          # Windows 启动器
codex-memory              # macOS / Linux 启动器
```

## 隐私边界

公开仓库中的 `memory/` 只放模板。不要提交个人版 `MEMORY.md`、分类文件、归档、日志、密钥或本机绝对路径。提交前可检查：

```sh
python -m unittest tests.test_public_template -v
```

检查器会限制单文件读取大小，并对配置的隐私模式进行匹配；它不会把记忆上传到网络，也不会替你判断外部服务的隐私政策。

## 开发与测试

在 Python 3.11 或更新版本中运行：

```sh
python -m compileall -q codex_memory tests hooks
python -m unittest discover -v
python -m codex_memory check --root memory
python scripts/install_smoke.py
```

其中 `scripts/install_smoke.py` 会在隔离临时目录中验证首次安装、更新、只读检查和当前平台包装器，不会修改用户默认的记忆目录。

CI 工作流见 [.github/workflows/ci.yml](./.github/workflows/ci.yml)。贡献代码时，请同时补充测试，并确认文档中的命令与实际入口一致。

## 版本与发布

版本号记录在 [VERSION](./VERSION)，变化见 [CHANGELOG.md](./CHANGELOG.md)。GitHub 简介、Topics 和发布前检查清单见 [docs/github-description.md](./docs/github-description.md)。英文安装步骤见 [docs/installation-en.md](./docs/installation-en.md)。
