# GitHub 主页发布文案

## 仓库简介（Description）

一个轻量、可审计、可迁移的 Codex 文件型记忆系统模板，支持分类记忆、冷归档、自检、原子写入和可选安全钩子。

## 主页正文（About / README 开场）

如果你希望 Codex 拥有长期记忆，但又不想把所有对话交给数据库或第三方服务，这个模板提供了一套简单的文件型方案：

- 用 `MEMORY.md` 作为热层入口；
- 用 `spaces/` 按主题拆分上下文；
- 用 `ARCHIVE/` 保存历史，不污染日常上下文；
- 用 `check-memory.ps1` 做结构和内容级自检；
- 用 `install.ps1` 安全安装并自动备份；
- 按需启用高风险工具调用拦截钩子。

它适合个人开发者、学生、研究者和希望掌控本地上下文的人。模板不上传数据，也不会自动修改你的模型、权限、网络或其他 Codex 配置。

## 推荐 Topics

```text
codex
openai-codex
ai-memory
agent-memory
context-engineering
powershell
privacy
developer-tools
```

## 发布前检查

- [ ] 确认仓库中没有个人版 `MEMORY.md`、真实归档或密钥；
- [ ] 确认 `LICENSE`、`README.md` 和 `CHANGELOG.md` 已提交；
- [ ] 在干净目录执行一次 `install.ps1`；
- [ ] 自检输出 `PASS: memory structure OK`；
- [ ] 在 GitHub 设置仓库简介和 Topics；
- [ ] 首次发布使用 `v0.1.0` 标签。
