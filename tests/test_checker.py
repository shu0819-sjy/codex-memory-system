import tempfile
import unittest
import os
import shutil
import subprocess
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

from codex_memory.checker import CheckResult, checkMemory
from codex_memory.config import DEFAULT_CONFIG
from tests.helpers import createMemoryFixture


class CheckerTestCase(unittest.TestCase):
  """功能：验证记忆自检器的诊断准确性与只读性。

  入参：无，unittest 负责构造测试实例。
  返回值：无，通过断言报告结果。
  边界情况：每个测试都在独立临时目录运行。
  """

  def setUp(self) -> None:
    """功能：为单个测试创建最小有效记忆结构。

    入参：无。
    返回值：无。
    边界情况：临时目录会在 tearDown 中清理。
    """
    self.tempDirectory = tempfile.TemporaryDirectory()
    self.memoryRoot = createMemoryFixture(Path(self.tempDirectory.name))

  def tearDown(self) -> None:
    """功能：清理单个测试使用的临时目录。

    入参：无。
    返回值：无。
    边界情况：目录内容已变化或为空时均可清理。
    """
    self.tempDirectory.cleanup()

  def snapshotFixture(self) -> dict[str, bytes | None]:
    """功能：快照记忆目录内的全部路径和文件字节。

    入参：无。
    返回值：相对路径到字节的映射，目录的值为 None。
    边界情况：显式记录空目录，以便检测路径清单变化。
    """
    snapshot: dict[str, bytes | None] = {}
    for path in sorted(self.memoryRoot.rglob('*')):
      relativePath = path.relative_to(self.memoryRoot).as_posix()
      isJunction = getattr(path, 'is_junction', lambda: False)()
      if path.is_symlink() or isJunction:
        snapshot[relativePath] = f'LINK:{os.readlink(path)}'.encode('utf-8')
      else:
        snapshot[relativePath] = path.read_bytes() if path.is_file() else None
    return snapshot

  def replaceWithSymlink(
    self,
    linkPath: Path,
    targetPath: Path,
    targetIsDirectory: bool,
  ) -> None:
    """功能：将 fixture 内的路径替换为指向外部哨兵的符号链接。

    入参：linkPath 为 fixture 内链接路径，targetPath 为 memoryRoot 外目标，targetIsDirectory 指明目标类型。
    返回值：无。
    边界情况：当前平台或权限不允许创建符号链接时跳过当前测试。
    """
    if linkPath.is_dir():
      shutil.rmtree(linkPath)
    else:
      linkPath.unlink()
    try:
      linkPath.symlink_to(targetPath, target_is_directory=targetIsDirectory)
    except OSError as error:
      if os.name != 'nt':
        self.skipTest(f'当前环境无法创建符号链接: {error}')
      junctionTarget = targetPath
      if not targetIsDirectory:
        junctionTarget = targetPath.with_name(f'{targetPath.name}-junction-target')
        junctionTarget.mkdir()
        shutil.copy2(targetPath, junctionTarget / 'sentinel.md')
      try:
        subprocess.run(
          ['cmd', '/c', 'mklink', '/J', str(linkPath), str(junctionTarget)],
          check=True,
          capture_output=True,
        )
      except (OSError, subprocess.CalledProcessError) as junctionError:
        self.skipTest(f'当前环境无法创建链接或 junction: {junctionError}')

  def assertDiagnostic(
    self,
    results: list[CheckResult],
    severity: str,
    path: str,
    messagePart: str,
  ) -> None:
    """功能：断言结果中存在指定级别、路径和消息关键词的诊断。

    入参：results 为诊断列表，severity 为级别，path 为相对路径，messagePart 为稳定关键词。
    返回值：无，匹配失败时由 unittest 报错。
    边界情况：允许其他诊断同时存在，不依赖结果顺序。
    """
    self.assertTrue(
      any(
        result.severity == severity
        and result.path == path
        and messagePart in result.message
        for result in results
      ),
      results,
    )

  def testValidFixturePassesWithoutMutation(self) -> None:
    """功能：验证有效结构返回 PASS 且检查全程只读。

    入参：无。
    返回值：无，通过诊断和字节快照断言报告结果。
    边界情况：同时比较文件字节、目录和路径清单。
    """
    before = self.snapshotFixture()

    results = checkMemory(self.memoryRoot, DEFAULT_CONFIG)

    self.assertTrue(any(result.severity == 'PASS' for result in results))
    self.assertFalse(any(result.severity == 'FAIL' for result in results), results)
    self.assertEqual(self.snapshotFixture(), before)

  def testMissingRequiredPathsFail(self) -> None:
    """功能：验证每个必需文件或目录缺失时报告其相对路径。

    入参：无。
    返回值：无，通过子测试诊断断言报告结果。
    边界情况：每次子测试重建 fixture，避免缺失项相互干扰。
    """
    missingPaths = ('MEMORY.md', 'RULES.md', 'spaces', 'ARCHIVE')
    for relativePath in missingPaths:
      with self.subTest(relativePath=relativePath):
        self.tempDirectory.cleanup()
        self.tempDirectory = tempfile.TemporaryDirectory()
        self.memoryRoot = createMemoryFixture(Path(self.tempDirectory.name))
        targetPath = self.memoryRoot / relativePath
        if targetPath.is_dir():
          for childPath in targetPath.iterdir():
            childPath.unlink()
          targetPath.rmdir()
        else:
          targetPath.unlink()

        results = checkMemory(self.memoryRoot, DEFAULT_CONFIG)

        self.assertDiagnostic(results, 'FAIL', relativePath, '缺失')

  def testMissingCategoryAndIndexMismatchFailWithLocation(self) -> None:
    """功能：验证分类文件缺失与索引不一致都可定位。

    入参：无。
    返回值：无，通过路径和行号断言报告结果。
    边界情况：同时覆盖配置分类缺文件和索引多出未配置分类。
    """
    (self.memoryRoot / 'spaces' / '学业.md').unlink()
    memoryPath = self.memoryRoot / 'MEMORY.md'
    memoryPath.write_text(
      memoryPath.read_text(encoding='utf-8') + '- 额外 → `spaces/额外.md`\n',
      encoding='utf-8',
    )

    results = checkMemory(self.memoryRoot, DEFAULT_CONFIG)

    self.assertDiagnostic(results, 'FAIL', 'spaces/学业.md', '缺失')
    indexResults = [
      result for result in results
      if result.path == 'MEMORY.md' and '索引' in result.message
    ]
    self.assertTrue(indexResults, results)
    self.assertTrue(any(result.line is not None for result in indexResults))

  def testBomAndConfiguredSizeLimitsAreDiagnosed(self) -> None:
    """功能：验证 BOM 和三类可配置大小上限都会产生诊断。

    入参：无。
    返回值：无，通过分类路径与消息断言报告结果。
    边界情况：阈值设为小于现有文件的 1 字节，避免依赖固定 fixture 长度。
    """
    categoryPath = self.memoryRoot / 'spaces' / '学业.md'
    categoryPath.write_bytes(b'\xef\xbb\xbf' + categoryPath.read_bytes())
    config = replace(
      DEFAULT_CONFIG,
      hotLimitBytes=(self.memoryRoot / 'MEMORY.md').stat().st_size - 1,
      rulesLimitBytes=(self.memoryRoot / 'RULES.md').stat().st_size - 1,
      spaceLimitBytes=categoryPath.stat().st_size - 1,
    )

    results = checkMemory(self.memoryRoot, config)

    self.assertDiagnostic(results, 'FAIL', 'MEMORY.md', '超过')
    self.assertDiagnostic(results, 'FAIL', 'RULES.md', '超过')
    self.assertDiagnostic(results, 'FAIL', 'spaces/学业.md', '超过')
    self.assertDiagnostic(results, 'FAIL', 'spaces/学业.md', 'BOM')

  def testEntryDatesAndActiveLimitsUseConfig(self) -> None:
    """功能：验证日期分隔符、进行中数量和过期阈值使用配置诊断。

    入参：无。
    返回值：无，通过路径、行号和消息断言报告结果。
    边界情况：设置进行中上限为 1，并使用明确超过 staleDays 的日期。
    """
    staleDate = date.today() - timedelta(days=3)
    memoryPath = self.memoryRoot / 'MEMORY.md'
    memoryPath.write_text(
      '\n'.join((
        '# Codex 运行记忆',
        '',
        '## 进行中（≤3 条；会话结束时更新：目标＋卡点＋下一步）',
        f'- 任务甲｜更新：{staleDate.isoformat()}',
        f'- 任务乙｜更新：{date.today().isoformat()}',
        '',
        '## 分类索引',
        *[f'- {category} → `spaces/{category}.md`' for category in DEFAULT_CONFIG.categories],
        '',
        '- 2026-10-01 缺少分隔符',
        '',
      )),
      encoding='utf-8',
    )
    config = replace(DEFAULT_CONFIG, activeTaskLimit=1, staleDays=1)

    results = checkMemory(self.memoryRoot, config)

    self.assertDiagnostic(results, 'FAIL', 'MEMORY.md', '进行中')
    self.assertDiagnostic(results, 'WARN', 'MEMORY.md', '过期')
    malformedResults = [
      result for result in results
      if result.path == 'MEMORY.md' and '分隔符' in result.message
    ]
    self.assertTrue(malformedResults, results)
    self.assertTrue(all(result.line is not None for result in malformedResults))

  def testDuplicateEntriesAreDiagnosedWithoutBodies(self) -> None:
    """功能：验证跨文件重复记忆条目被诊断且不回显正文。

    入参：无。
    返回值：无，通过重复消息与脱敏断言报告结果。
    边界情况：相同条目出现在两个不同分类文件中。
    """
    secretBody = '绝不应出现的记忆正文'
    duplicateEntry = f'- 2026-10-01｜{secretBody}\n'
    for category in ('学业', '项目'):
      (self.memoryRoot / 'spaces' / f'{category}.md').write_text(
        f'# {category}\n\n{duplicateEntry}',
        encoding='utf-8',
      )

    results = checkMemory(self.memoryRoot, DEFAULT_CONFIG)
    rendered = '\n'.join(
      f'{result.severity}|{result.message}|{result.path}|{result.line}'
      for result in results
    )

    self.assertTrue(any('重复' in result.message for result in results), results)
    self.assertNotIn(secretBody, rendered)

  def testArchiveFilenameUsesConfiguredPattern(self) -> None:
    """功能：验证归档文件名按配置正则检查。

    入参：无。
    返回值：无，通过归档相对路径与消息断言报告结果。
    边界情况：`.gitkeep` 不参与命名检查，本测试使用明确不匹配的 Markdown 文件。
    """
    (self.memoryRoot / 'ARCHIVE' / 'bad-name.md').write_text('# archive\n', encoding='utf-8')

    results = checkMemory(self.memoryRoot, DEFAULT_CONFIG)

    self.assertDiagnostic(results, 'WARN', 'ARCHIVE/bad-name.md', '命名')

  def testPrivacyMatchesAreCaseInsensitiveAndRedacted(self) -> None:
    """功能：验证隐私字面量按大小写无关方式检测且诊断完全脱敏。

    入参：无。
    返回值：无，通过行号、路径及诊断文本断言报告结果。
    边界情况：配置模式为小写，fixture 使用大写匹配和敏感样例值。
    """
    matchedLiteral = 'api_key='
    secretValue = 'DO-NOT-RENDER-THIS-VALUE'
    categoryPath = self.memoryRoot / 'spaces' / '项目.md'
    categoryPath.write_text(
      f'# 项目\n\n- 2026-10-01｜API_KEY={secretValue}\n',
      encoding='utf-8',
    )
    config = replace(DEFAULT_CONFIG, privacyPatterns=(matchedLiteral,))

    results = checkMemory(self.memoryRoot, config)
    rendered = '\n'.join(
      f'{result.severity}|{result.message}|{result.path}|{result.line}'
      for result in results
    )

    privacyResults = [result for result in results if '隐私' in result.message]
    self.assertTrue(privacyResults, results)
    self.assertTrue(all(result.path == 'spaces/项目.md' for result in privacyResults))
    self.assertTrue(all(result.line == 3 for result in privacyResults))
    self.assertNotIn(matchedLiteral, rendered.lower())
    self.assertNotIn(secretValue, rendered)

  def testRootFileSymlinksAreRejectedWithoutReadingTargets(self) -> None:
    """功能：验证 MEMORY.md 和 RULES.md 链接被拒绝且外部目标不被读取。

    入参：无。
    返回值：无，通过链接路径和隐私诊断断言报告结果。
    边界情况：两个根文件分别在独立子测试中替换，外部哨兵含隐私字面量。
    """
    for fileName in ('MEMORY.md', 'RULES.md'):
      with self.subTest(fileName=fileName):
        self.tempDirectory.cleanup()
        self.tempDirectory = tempfile.TemporaryDirectory()
        tempRoot = Path(self.tempDirectory.name)
        self.memoryRoot = createMemoryFixture(tempRoot)
        sentinelPath = tempRoot / f'external-{fileName}'
        sentinelPath.write_text('API_KEY=OUTSIDE-SENTINEL\n- 2026-10-01 bad\n', encoding='utf-8')
        self.replaceWithSymlink(self.memoryRoot / fileName, sentinelPath, False)
        before = self.snapshotFixture()

        results = checkMemory(self.memoryRoot, DEFAULT_CONFIG)

        self.assertDiagnostic(results, 'FAIL', fileName, '链接')
        self.assertEqual(
          {result.path for result in results if result.severity != 'PASS'},
          {fileName},
        )
        self.assertFalse(any('隐私' in result.message for result in results), results)
        self.assertFalse(any(result.path and result.path.startswith('..') for result in results), results)
        self.assertEqual(self.snapshotFixture(), before)

  def testCategoryFileSymlinkIsRejectedWithoutReadingTarget(self) -> None:
    """功能：验证配置分类文件链接被拒绝且外部目标不被读取。

    入参：无。
    返回值：无，通过链接路径、隐私诊断和只读快照断言报告结果。
    边界情况：外部哨兵同时包含隐私字面量与可解析记忆条目。
    """
    tempRoot = Path(self.tempDirectory.name)
    sentinelPath = tempRoot / 'external-category.md'
    sentinelPath.write_text('- 2026-10-01｜API_KEY=OUTSIDE-SENTINEL\n', encoding='utf-8')
    categoryPath = self.memoryRoot / 'spaces' / '学业.md'
    self.replaceWithSymlink(categoryPath, sentinelPath, False)
    before = self.snapshotFixture()

    results = checkMemory(self.memoryRoot, DEFAULT_CONFIG)

    self.assertDiagnostic(results, 'FAIL', 'spaces/学业.md', '链接')
    self.assertEqual(
      {result.path for result in results if result.severity != 'PASS'},
      {'spaces/学业.md'},
    )
    self.assertFalse(any('隐私' in result.message for result in results), results)
    self.assertEqual(self.snapshotFixture(), before)

  def testRequiredDirectorySymlinksAreRejectedWithoutEnumeration(self) -> None:
    """功能：验证 spaces 和 ARCHIVE 目录链接被拒绝且外部目标不被枚举。

    入参：无。
    返回值：无，通过链接自身路径和外部哨兵零诊断断言报告结果。
    边界情况：两个必需目录分别在独立子测试中替换为外部目录链接。
    """
    for directoryName in ('spaces', 'ARCHIVE'):
      with self.subTest(directoryName=directoryName):
        self.tempDirectory.cleanup()
        self.tempDirectory = tempfile.TemporaryDirectory()
        tempRoot = Path(self.tempDirectory.name)
        self.memoryRoot = createMemoryFixture(tempRoot)
        sentinelRoot = tempRoot / f'external-{directoryName}'
        sentinelRoot.mkdir()
        (sentinelRoot / 'outside-sentinel.md').write_text(
          '- 2026-10-01｜API_KEY=OUTSIDE-SENTINEL\n',
          encoding='utf-8',
        )
        self.replaceWithSymlink(self.memoryRoot / directoryName, sentinelRoot, True)
        before = self.snapshotFixture()

        results = checkMemory(self.memoryRoot, DEFAULT_CONFIG)

        self.assertDiagnostic(results, 'FAIL', directoryName, '链接')
        self.assertEqual(
          {result.path for result in results if result.severity != 'PASS'},
          {directoryName},
        )
        self.assertFalse(any('outside-sentinel.md' == result.path for result in results), results)
        self.assertFalse(any('隐私' in result.message for result in results), results)
        self.assertEqual(self.snapshotFixture(), before)


if __name__ == '__main__':
  unittest.main()
