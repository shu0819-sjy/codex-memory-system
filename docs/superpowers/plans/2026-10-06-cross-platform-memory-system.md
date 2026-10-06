# 跨平台文件型记忆系统实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 Codex 文件型记忆模板改造成 Windows、macOS、Linux 均可安装、配置、自检和安全更新的项目，同时保护现有用户记忆。

**Architecture:** Python 标准库作为安装和检查核心；Windows PowerShell 与 macOS/Linux shell 仅作为轻量入口。工具代码保留在克隆仓库，目标目录只保存用户记忆、分类、归档和本地配置；检查只读，安装更新不得覆盖已有用户内容。

**Tech Stack:** Python 标准库、`unittest`、PowerShell、POSIX shell、GitHub Actions。

**Release:** `0.2.0`，升级来源 `0.1.0`。

**Spec:** `docs/superpowers/specs/2026-10-06-cross-platform-memory-system-design.md`

## Global Constraints

- 支持 Windows、macOS、Linux。
- 最低支持 Python 3.11；CI 版本矩阵为 3.11、3.12、3.13、3.14。
- 本轮目标版本为 `0.2.0`；旧版 `0.1.0` 用户数据必须原样保留。
- 初版不引入第三方 Python 运行依赖。
- 默认目标目录为用户主目录下的 `.codex-memory`，同时允许显式指定路径。
- 检查命令只读，不自动修复用户文件。
- 更新不得覆盖已存在的记忆正文、分类文件、规则、配置或归档；新增模板只补缺失文件。
- 不自动修改用户 `AGENTS.md`、Codex 配置、权限、模型或网络设置。
- 不上传聊天内容，不启用数据库、云同步或后台进程。
- 配置按不可信输入校验；分类名必须是单一文件名，拒绝空值、`.`、`..`、路径分隔符和控制字符；诊断不得回显记忆正文或敏感匹配文本。
- 安装不得跟随源模板符号链接；更新不得沿目标目录内部的符号链接写入。
- GitHub Actions 仅在临时目录测试安装，不触碰 runner 的用户目录。
- 不提交 Git、不推送、不创建 Release；这些操作需另行授权。
- 变量和函数名使用小驼峰，类名使用大驼峰，常量使用全大写加下划线；自定义函数用简体中文文档字符串说明功能、入参、返回值和边界情况。
- Python JSON 配置键和 dataclass 字段使用小驼峰；文件/模块名遵循平台惯例。

---

## 文件结构与职责

- `codex_memory/__init__.py`：包标识和版本常量。
- `codex_memory/__main__.py`：`python -m codex_memory` 入口。
- `codex_memory/config.py`：配置数据类型、默认值、JSON 加载和校验。
- `codex_memory/checker.py`：只读结构/内容检查和可定位诊断。
- `codex_memory/installer.py`：首次安装、缺失模板补入、备份和失败回滚。
- `codex_memory/cli.py`：参数解析、交互配置、命令分派和退出码。
- `codex-memory.ps1`：Windows 启动入口，定位 Python 并调用 CLI。
- `codex-memory`：macOS/Linux 启动入口，定位 Python 并调用 CLI。
- `memory/config.json`：公开模板默认检查配置，不含用户本机路径。
- `tests/test_config.py`：配置缺省、字段校验和分类规则测试。
- `tests/test_checker.py`：结构、内容、索引和只读性测试。
- `tests/test_installer.py`：安装、更新保留和回滚测试。
- `tests/test_cli.py`：命令参数、输出与退出码测试。
- `.github/workflows/ci.yml`：三平台和 Python 版本矩阵。
- `README.md`、`AGENTS.example.md`：安装、接入、检查、更新与恢复说明。
- `CHANGELOG.md`、`VERSION`：版本记录。

## Task 1: 建立 Python 包与可配置规则

**Files:**
- Create: `codex_memory/__init__.py`
- Create: `codex_memory/__main__.py`
- Create: `codex_memory/config.py`
- Create: `memory/config.json`
- Create: `tests/__init__.py`
- Create: `tests/test_config.py`
- Modify: `.gitignore`

**Interfaces:**
- `MemoryConfig`：不可变配置对象，字段为 `categories: tuple[str, ...]`、`hotLimitBytes: int`、`spaceLimitBytes: int`、`rulesLimitBytes: int`、`activeTaskLimit: int`、`staleDays: int`、`archiveNamePattern: str`、`privacyPatterns: tuple[str, ...]`。
- 默认值以当前 `memory/check-memory.ps1` 为基线：类别 `学业`、`出国申请`、`竞赛`、`项目`、`工具与环境`、`人设`、`任务要求`；阈值 `hotLimitBytes=10240`、`spaceLimitBytes=4096`、`rulesLimitBytes=4096`、`activeTaskLimit=3`、`staleDays=30`；归档正则 `^\d{4}-\d{2}-.+\.md$`。`privacyPatterns` 为大小写不敏感的字面子串：`api_key=`、`access_token=`、`password=`、`secret=`、`-----BEGIN PRIVATE KEY-----`、`C:\Users\`、`D:\`、`/Users/`、`/home/`。
- `loadConfig(memoryRoot: Path) -> MemoryConfig`：读取 `memoryRoot/config.json`；文件缺失时返回与公开模板规则一致的默认值；JSON 损坏、未知字段、字段类型错误、负阈值或无效正则表达式抛出 `ConfigError`。
- `python -m codex_memory` 在命令解析完成前能加载本地包；命令功能由后续任务接入。

- [ ] **Step 1: 写配置测试**：覆盖标准 JSON 加载、缺省配置、未知字段拒绝、必需字段缺失、错误类型、负阈值、无效归档正则和跨平台不安全分类名拒绝（空白、Windows 非法字符、尾随点/空格、保留设备名及其扩展名）；所有测试方法使用 camelCase 并带简体中文文档字符串；创建空的 `tests/__init__.py` 以使 unittest 模块可导入。
- [ ] **Step 2: 运行配置测试**：`python -m unittest tests.test_config -v`；预期因包与配置实现尚不存在而失败。
- [ ] **Step 3: 实现配置数据类型与默认配置**：在 `config.py` 用 `dataclasses.dataclass(frozen=True)` 定义 `MemoryConfig`，字段为 `hotLimitBytes`、`spaceLimitBytes`、`rulesLimitBytes`、`activeTaskLimit`、`staleDays`、`archiveNamePattern`、`privacyPatterns`；实现 `ConfigError` 和 `loadConfig`；对照数据类字段集合拒绝未知键；拒绝空白分类、Windows 非法文件名字符、尾随点/空格和大小写不敏感的 Windows 保留设备名（含扩展名）；`memory/config.json` 保存同一组公开默认值。
- [ ] **Step 4: 实现包入口并运行测试**：增加 `__init__.py`、`__main__.py`，再次运行配置测试，预期全部通过。
- [ ] **Step 5: 忽略 Python 缓存并检查补丁**：在 `.gitignore` 加入 `__pycache__/`、`*.py[cod]`；运行 `git diff --check`。

## Task 2: 迁移只读自检器

**Files:**
- Create: `codex_memory/checker.py`
- Create: `tests/helpers.py`
- Create: `tests/test_checker.py`

**Interfaces:**
- `CheckResult`：不可变对象，字段为 `severity: str`（仅 `PASS`、`WARN`、`FAIL`）、`message: str`、`path: str | None`、`line: int | None`。
- `checkMemory(memoryRoot: Path, config: MemoryConfig) -> list[CheckResult]`：检查根目录及其记忆文件；不写入、重命名或删除任何文件。
- `tests/helpers.py` 的 `createMemoryFixture(root: Path) -> Path`：创建最小有效热记忆、规则、分类和归档结构，返回记忆根目录。

- [ ] **Step 1: 写最小有效结构和缺文件测试**：使用临时目录 fixture，验证有效模板无 `FAIL`，缺 `MEMORY.md`、`RULES.md`、`spaces` 或 `ARCHIVE` 时报告具体路径。
- [ ] **Step 2: 运行检查器测试确认失败**：`python -m unittest tests.test_checker -v`；预期因检查器不存在而失败。
- [ ] **Step 3: 实现结构检查**：定义 `CheckResult` 和 `checkMemory`，实现必需文件/目录及 UTF-8 无 BOM 检查，不产生写入副作用。
- [ ] **Step 4: 加入索引与内容规则测试**：覆盖分类索引与配置不一致、分类文件缺失、热层/分类/规则超限、日期分隔符缺失、进行中条目超量或过期、重复条目、归档文件名异常和隐私模式命中。
- [ ] **Step 5: 实现索引与内容检查**：所有可配置规则从 `MemoryConfig` 读取；每条诊断包含相对路径，若问题来自具体文本行则包含行号；诊断不得包含整行记忆内容或命中的敏感文本。
- [ ] **Step 6: 验证只读性与全量测试**：在检查前后比较 fixture 文件的字节内容；运行 `python -m unittest tests.test_config tests.test_checker -v`，预期全部通过。

## Task 3: 实现安全安装与升级

**Files:**
- Create: `codex_memory/installer.py`
- Create: `tests/test_installer.py`

**Interfaces:**
- `InstallError`：安装或更新无法完成时抛出的异常。
- `InstallReport`：不可变对象，字段为 `targetRoot: Path`、`createdPaths: tuple[Path, ...]`、`backupPath: Path | None`、`templateVersion: str`。
- `installTemplate(repositoryRoot: Path, targetRoot: Path, update: bool = False) -> InstallReport`：从 `repositoryRoot/memory` 复制模板，读取 `repositoryRoot/VERSION`；首次安装要求目标不存在；更新要求目标存在；更新时只新增缺失模板和 `.template-version`，不覆盖已有任何路径。

- [ ] **Step 1: 写首次安装与默认拒绝覆盖测试**：验证完整复制公开模板、目标存在时不带 `update` 抛出 `InstallError`，并验证异常前后原文件哈希相同。
- [ ] **Step 2: 运行安装器测试确认失败**：`python -m unittest tests.test_installer -v`；预期因安装器不存在而失败。
- [ ] **Step 3: 实现首次安装**：先在目标同级临时目录构造 `MEMORY.md`、`RULES.md`、`spaces/`、`ARCHIVE/`、`config.json` 和 `.template-version`，再将其移至目标；异常时删除仅由本次安装创建的临时路径。
- [ ] **Step 4: 写更新保护测试**：目标内放置自定义 `MEMORY.md`、`RULES.md`、分类、配置和归档；更新后逐个校验字节不变，同时验证新版本缺少的模板文件补入。
- [ ] **Step 5: 实现安全更新**：预检源模板与目标目录树并拒绝符号链接；扫描源模板，只复制目标缺少的相对路径；不覆盖文件或目录；更新 `.template-version` 前在同目录创建可恢复备份。
- [ ] **Step 6: 写失败回滚测试并实现回滚**：通过临时目录权限或注入文件操作故障模拟中途失败；确认本次新增项移除、被触碰的元数据恢复、用户文件字节不变，并在异常结果中保留备份位置。
- [ ] **Step 7: 验证跨平台路径边界**：测试目标路径含空格和中文、目标父目录不存在、目标已存在但不可写、源模板含符号链接、目标目录树含符号链接；运行 `python -m unittest tests.test_installer -v`，预期全部通过。

## Task 4: CLI、交互配置与平台入口

**Files:**
- Create: `codex_memory/cli.py`
- Create: `tests/test_cli.py`
- Create: `codex-memory.ps1`
- Create: `codex-memory`
- Modify: `codex_memory/__main__.py`

**Interfaces:**
- `buildParser() -> argparse.ArgumentParser`：定义 `install`、`check`、`instructions` 子命令，以及 `--root`、`--update`、`--non-interactive` 参数。
- `main(argv: Sequence[str] | None = None) -> int`：执行命令；成功返回 `0`，检查失败返回 `1`，参数/配置/运行错误返回 `2`。
- `instructions` 输出 Windows、macOS、Linux 可复制的 `AGENTS.md` 接入文本，不写入该文件。

- [ ] **Step 1: 写 CLI 行为测试**：覆盖默认根目录、显式 `--root`、交互选择安装路径、非交互缺少必需参数、check 的 0/1/2 退出码及 instructions 不修改磁盘。
- [ ] **Step 2: 运行 CLI 测试确认失败**：`python -m unittest tests.test_cli -v`；预期因 CLI 实现不存在而失败。
- [ ] **Step 3: 实现参数解析与非交互命令**：用 `argparse` 实现三个子命令并调用前述模块；通过 `main(argv)` 使测试无需启动子进程。
- [ ] **Step 4: 实现交互安装**：仅在输入输出均为终端且未指定 `--non-interactive` 时询问目标目录；默认展示 `.codex-memory`；拒绝覆盖时说明如何执行安全更新。
- [ ] **Step 5: 实现跨平台入口**：PowerShell 入口使用 `python`/`py` 发现逻辑；POSIX 入口使用 `python3` 后回退到 `python`；两者以参数数组透传所有参数和退出码，不拼接用户输入、不执行下载或提权。
- [ ] **Step 6: 运行 CLI 与入口验收**：运行全体 `unittest`；在 Windows 执行 PowerShell 安装、检查和说明命令，在 POSIX shell 对入口执行语法检查并运行相同 CLI 核心测试。

## Task 5: 三平台 CI 与公开模板隐私扫描

**Files:**
- Modify: `.github/workflows/ci.yml`
- Create: `tests/test_public_template.py`

**Interfaces:**
- CI 矩阵组合 `windows-latest`、`macos-latest`、`ubuntu-latest` 与 Python `3.11`、`3.12`、`3.13`、`3.14`；最低版本及矩阵也写入 README。
- 隐私扫描只扫描 `memory/`、`README.md`、`AGENTS.example.md`、`docs/` 中的公开内容，不扫描含正向测试用例的 `tests/test_public_template.py`。
- `tests/test_public_template.py` 验证仓库模板及示例不含个人绝对路径、真实令牌格式或用户专属记忆文本。

- [ ] **Step 1: 写公开模板扫描测试**：加入已知安全模板 PASS 用例与包含 `C:\Users\`、`/Users/`、`/home/`、密钥赋值特征的临时 fixture FAIL 用例。
- [ ] **Step 2: 运行扫描测试确认失败**：`python -m unittest tests.test_public_template -v`；预期因扫描器尚未实现而失败。
- [ ] **Step 3: 实现可维护的隐私规则**：将公开仓库扫描规则放在专用测试模块，排除 README 中用于说明扫描器的字面示例，报告命中文件和行号。
- [ ] **Step 4: 扩展 GitHub Actions**：设置 3 OS × 4 Python 版本矩阵，运行语法编译、单元测试、模板自检和隐私扫描；安装/更新测试使用 `$env:RUNNER_TEMP` 或系统临时目录。
- [ ] **Step 5: 本地运行完整 CI 命令**：执行 `python -m compileall codex_memory tests`、`python -m unittest discover -v` 和 `python -m codex_memory check --root memory`；逐项记录退出码。

## Task 6: 文档、升级说明与最终验收

**Files:**
- Modify: `README.md`
- Modify: `AGENTS.example.md`
- Modify: `CHANGELOG.md`
- Modify: `VERSION`
- Modify: `docs/github-description.md`
- Create: `docs/installation-en.md`

**Interfaces:**
- README 的三平台命令必须逐字对应当前 CLI 和包装脚本。
- 升级说明从 `0.1.0` 开始：保留目标记忆目录，先更新克隆仓库，再运行 `install --update`；不得使用旧 `-Force` 替换整目录。

- [ ] **Step 1: 更新三平台快速开始**：说明 Python 前置条件、克隆、Windows/macOS/Linux 安装命令、指定目录、只读自检及复制接入片段。
- [ ] **Step 2: 更新中英双语配置与数据保护说明**：在 README 和 `docs/installation-en.md` 说明 JSON 配置字段、交互/非交互模式、检查退出码、更新只补文件的行为、备份和恢复路径；明确不会上传对话或自动编辑 Codex 配置。
- [ ] **Step 3: 更新版本记录和仓库简介**：将 `VERSION` 设为 `0.2.0`，按实际实现更新 `CHANGELOG.md` 和 GitHub 描述建议文档，不添加未验收能力声明。
- [ ] **Step 4: 完整验收**：在可用本机环境执行 `python -m compileall codex_memory tests`、`python -m unittest discover -v`、三平台入口可用的本机冒烟测试、`python -m codex_memory check --root memory`、公开隐私扫描和 `git diff --check`。
- [ ] **Step 5: 审查改动边界**：确认无个人记忆、机器绝对路径、密钥、临时测试数据；确认工作树只包含本计划关联文件及设计/计划文档；不提交、不推送。

## 覆盖自查

- 跨平台 Python 核心与三平台入口：Task 1、Task 4。
- 默认路径、配置和新用户安装：Task 1、Task 3、Task 4。
- 记忆结构与只读自检：Task 1、Task 2。
- 用户记忆保留、备份和失败回滚：Task 3。
- 三平台 CI、Python 版本支持范围和隐私扫描：Task 5。
- README、接入、升级、版本和发布前验收：Task 6。
- 安全边界和不推送要求：所有任务及 Global Constraints。
