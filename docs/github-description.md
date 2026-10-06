# GitHub 主页发布文案

## 仓库简介（Description）

跨 Windows、macOS 和 Linux 的 Codex 文件型记忆系统模板，提供安全安装、只读检查、分类记忆、冷归档和可选安全钩子。

## 主页正文（About / README 开场）

如果你希望 Codex 拥有长期记忆，但又不想把所有对话交给数据库或第三方服务，这个模板提供了一套本地文件方案：

- 用 `MEMORY.md` 作为热层入口；
- 用 `spaces/` 按主题拆分上下文；
- 用 `ARCHIVE/` 保存历史，不污染日常上下文；
- 用 `python -m codex_memory check` 做只读结构和隐私检查；
- 用 `install --update` 安全补齐缺失模板，不覆盖已有记忆；
- 按需启用高风险工具调用拦截钩子。

它适合个人开发者、学生、研究者和希望掌控本地上下文的人。模板不上传数据，也不会自动修改模型、权限、网络或其他 Codex 配置。项目要求 Python 3.11+，并提供 Windows、macOS 和 Linux 的调用方式。

## 推荐 Topics

```text
codex
openai-codex
ai-memory
agent-memory
context-engineering
python
privacy
developer-tools
```

## 发布前检查

- [ ] 确认仓库中没有个人版 `MEMORY.md`、真实归档、日志或密钥；
- [ ] 确认 `LICENSE`、`README.md` 和 `CHANGELOG.md` 已提交；
- [ ] 在干净目录运行 `python -m codex_memory install --non-interactive`；
- [ ] 运行 `python -m codex_memory check --root '<目标目录>'` 并检查返回码；
- [ ] 运行 `python -m unittest discover -v` 和公开模板隐私测试；
- [ ] 在 GitHub 设置仓库简介和 Topics；
- [ ] 发布前确认 CI 的 Windows、macOS、Ubuntu 矩阵均为绿色；
- [ ] 首次 0.2.0 发布使用 `v0.2.0` 标签。
