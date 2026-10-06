import json
from pathlib import Path

from codex_memory.config import DEFAULT_CONFIG


def createMemoryFixture(root: Path) -> Path:
  """功能：在指定临时路径创建最小有效记忆结构。

  入参：root 为容纳记忆目录的临时根路径。
  返回值：创建完成的 memory 根目录。
  边界情况：root 已存在时只在其下创建 memory，不访问用户真实记忆。
  """
  memoryRoot = root / 'memory'
  spacesRoot = memoryRoot / 'spaces'
  archiveRoot = memoryRoot / 'ARCHIVE'
  spacesRoot.mkdir(parents=True)
  archiveRoot.mkdir()

  indexLines = [
    f'- {category} → `spaces/{category}.md`'
    for category in DEFAULT_CONFIG.categories
  ]
  memoryText = '\n'.join((
    '# Codex 运行记忆',
    '',
    '## 进行中（≤3 条；会话结束时更新：目标＋卡点＋下一步）',
    '',
    '## 分类索引',
    '',
    *indexLines,
    '',
  ))
  (memoryRoot / 'MEMORY.md').write_text(memoryText, encoding='utf-8')
  (memoryRoot / 'RULES.md').write_text('# 记忆写入规范\n', encoding='utf-8')
  for category in DEFAULT_CONFIG.categories:
    (spacesRoot / f'{category}.md').write_text(
      f'# {category}\n\nTEMPLATE_PLACEHOLDER\n',
      encoding='utf-8',
    )
  configData = {
    'categories': list(DEFAULT_CONFIG.categories),
    'hotLimitBytes': DEFAULT_CONFIG.hotLimitBytes,
    'spaceLimitBytes': DEFAULT_CONFIG.spaceLimitBytes,
    'rulesLimitBytes': DEFAULT_CONFIG.rulesLimitBytes,
    'activeTaskLimit': DEFAULT_CONFIG.activeTaskLimit,
    'staleDays': DEFAULT_CONFIG.staleDays,
    'archiveNamePattern': DEFAULT_CONFIG.archiveNamePattern,
    'privacyPatterns': list(DEFAULT_CONFIG.privacyPatterns),
  }
  (memoryRoot / 'config.json').write_text(
    json.dumps(configData, ensure_ascii=False, indent=2) + '\n',
    encoding='utf-8',
  )
  return memoryRoot

