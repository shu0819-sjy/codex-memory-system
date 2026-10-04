# Codex 文件型记忆系统模板

这是一个面向 Codex 用户的轻量级、可审计、可迁移记忆系统模板。

每次 push 或 Pull Request 都会自动运行结构检查、钩子语法检查、安装测试和公开仓库脱敏扫描。

当前版本：`0.1.0`。许可证：MIT。

它不依赖数据库，不上传对话内容，也不会自动把所有聊天写入记忆。系统由热记忆、分类记忆、冷归档和只读自检组成，适合希望自己掌控数据的人。

## 特点

- 文件系统存储，内容可直接查看、备份和迁移；
- 热层只保留索引、进行中任务和可复用错误模式；
- 分类文件按任务主题加载，减少上下文污染；
- 归档区只查历史，不全量载入；
- 自检脚本检查大小、编码、重复条目、日期、进行中任务数量和归档命名；
- 安装前自动备份已有目录，不直接覆盖；
- 可选的 `PreToolUse` 安全钩子用于拦截敏感凭据读取和高破坏性命令。

## 快速安装（Windows PowerShell）

在仓库根目录运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
```

默认安装到：

```text
%USERPROFILE%\.codex-memory
```

也可以指定目录：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -InstallRoot 'D:\你的路径\memory'
```

如果目标目录已经存在，脚本会停止；确认要替换时使用 `-Force`，旧目录会先改名备份。

## 接入 Codex 工作流

将下面的约束加入你自己的 `AGENTS.md` 或等效规则文件，并把路径替换为实际安装位置：

```text
每个新会话先读取 <安装目录>\MEMORY.md。
按 MEMORY.md 的分类索引读取最多两个相关分类文件。
写入记忆前读取 <安装目录>\RULES.md。
禁止全量读取 ARCHIVE；历史只按日期和主题检索。
```

记忆系统不会自动修改你的模型、权限、网络或中转配置。

## 手动自检

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\memory\check-memory.ps1
```

输出 `PASS: memory structure OK` 即表示结构检查通过。`WARN` 是提醒，不会阻止使用；`FAIL` 需要先修复。

## 可选安全钩子

`hooks\pre_tool_guard.py` 是可选组件。它只处理两类风险：

1. 读取或复制 `.env`、私钥、凭据等敏感文件；
2. 强制重置、批量删除、停止进程、关机等高破坏性命令。

它不会自动修改现有 `hooks.json`。请先备份自己的配置，再按当前 Codex 版本的 Hooks 文档将脚本接入 `PreToolUse`。普通命令会返回 `continue: true`，解析异常也会放行，避免钩子故障阻塞工作。

## 目录结构

```text
memory/
├─ MEMORY.md              # 热层入口
├─ RULES.md               # 写入规范
├─ check-memory.ps1       # 只读自检
├─ spaces/                # 分类记忆模板
└─ ARCHIVE/               # 冷归档
hooks/
└─ pre_tool_guard.py      # 可选安全钩子
install.ps1               # 安装和备份
```

## 隐私与公开发布

仓库中的 `memory/` 只放模板，不要把个人版 `MEMORY.md`、分类文件、归档、密钥、日志或本机绝对路径提交到公开仓库。建议先执行：

```powershell
rg -n -i 'api[_-]?key|token|secret|password|private[_-]?key|D:\\|C:\\Users\\' .
```

确认没有个人信息后再提交。

## 许可证

本项目采用 [MIT License](./LICENSE)。版本变化见 [CHANGELOG.md](./CHANGELOG.md)。

GitHub 主页简介、推荐 Topics 和发布前检查清单见 [docs/github-description.md](./docs/github-description.md)。
