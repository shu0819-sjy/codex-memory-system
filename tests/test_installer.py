import errno
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import codex_memory.installer as installerModule
from codex_memory.installer import InstallError, installTemplate


class InstallerTestCase(unittest.TestCase):
  """功能：验证跨平台模板安装、保留更新、回滚和链接安全边界。

  入参：无，unittest 负责构造测试实例。
  返回值：无，通过断言报告结果。
  边界情况：所有读写均限制在每个测试独立的临时目录内。
  """

  def setUp(self) -> None:
    """功能：创建隔离的仓库模板副本和安装目标基准路径。

    入参：无。
    返回值：无。
    边界情况：复制当前公开模板，但不访问用户真实记忆目录。
    """
    self.tempDirectory = tempfile.TemporaryDirectory()
    self.tempRoot = Path(self.tempDirectory.name)
    self.projectRoot = Path(__file__).resolve().parents[1]
    self.repositoryRoot = self.tempRoot / 'repository source'
    shutil.copytree(self.projectRoot / 'memory', self.repositoryRoot / 'memory')
    (self.repositoryRoot / 'VERSION').write_text('0.2.0\n', encoding='utf-8')

  def tearDown(self) -> None:
    """功能：清理当前测试创建的临时仓库、目标和外部哨兵。

    入参：无。
    返回值：无。
    边界情况：测试提前失败时仍由 TemporaryDirectory 尝试完整清理。
    """
    self.tempDirectory.cleanup()

  def snapshotTree(self, root: Path) -> dict[str, bytes | None]:
    """功能：在不跟随链接的前提下快照目录路径和文件字节。

    入参：root 为待快照目录。
    返回值：相对路径到文件字节的映射，目录使用 None，链接记录目标文本。
    边界情况：root 不存在时返回空映射，链接目录不会被递归枚举。
    """
    if not root.exists() and not root.is_symlink():
      return {}
    snapshot: dict[str, bytes | None] = {}
    pendingPaths = [root]
    while pendingPaths:
      currentPath = pendingPaths.pop()
      if currentPath != root:
        relativePath = currentPath.relative_to(root).as_posix()
        if self.isLink(currentPath):
          snapshot[relativePath] = f'LINK:{os.readlink(currentPath)}'.encode('utf-8')
          continue
        snapshot[relativePath] = currentPath.read_bytes() if currentPath.is_file() else None
      if currentPath.is_dir() and not self.isLink(currentPath):
        pendingPaths.extend(sorted(currentPath.iterdir(), reverse=True))
    return snapshot

  def isLink(self, path: Path) -> bool:
    """功能：识别符号链接以及当前 Python 可识别的 Windows junction。

    入参：path 为待判断路径。
    返回值：路径是链接或 junction 时返回 True，否则返回 False。
    边界情况：Python 3.11 没有 Path.is_junction 时仅使用符号链接判断。
    """
    isJunction = getattr(path, 'is_junction', lambda: False)()
    return path.is_symlink() or isJunction

  def replaceWithDirectoryLink(self, linkPath: Path, targetPath: Path) -> None:
    """功能：把目录替换为外部目录链接，必要时在 Windows 回退到 junction。

    入参：linkPath 为链接位置，targetPath 为外部目录目标。
    返回值：无。
    边界情况：平台和权限均不支持链接时跳过当前测试。
    """
    if linkPath.exists() and not self.isLink(linkPath):
      shutil.rmtree(linkPath)
    try:
      linkPath.symlink_to(targetPath, target_is_directory=True)
      return
    except OSError as symlinkError:
      if os.name != 'nt':
        self.skipTest(f'当前环境无法创建目录符号链接: {symlinkError}')
    try:
      subprocess.run(
        ['cmd', '/c', 'mklink', '/J', str(linkPath), str(targetPath)],
        check=True,
        capture_output=True,
      )
    except (OSError, subprocess.CalledProcessError) as junctionError:
      self.skipTest(f'当前环境无法创建目录链接或 junction: {junctionError}')

  def replaceWithFileLink(self, linkPath: Path, targetPath: Path) -> None:
    """功能：把普通文件替换为指向外部哨兵的文件符号链接。

    入参：linkPath 为链接位置，targetPath 为外部普通文件目标。
    返回值：无。
    边界情况：当前平台或权限不允许文件符号链接时跳过当前测试。
    """
    linkPath.unlink()
    try:
      linkPath.symlink_to(targetPath)
    except OSError as error:
      self.skipTest(f'当前环境无法创建文件符号链接: {error}')

  def testFirstInstallCopiesTemplateAndRejectsOverwrite(self) -> None:
    """功能：验证首次安装复制数据模板和版本，并拒绝覆盖已存在目标。

    入参：无。
    返回值：无，通过安装报告、文件清单和拒绝覆盖后的快照断言报告结果。
    边界情况：目标路径包含中文和空格，且多级父目录起初不存在。
    """
    targetRoot = self.tempRoot / '不存在父目录' / '含 空格' / '记忆目录'

    report = installTemplate(self.repositoryRoot, targetRoot)

    self.assertEqual(report.targetRoot, targetRoot)
    self.assertEqual(report.templateVersion, '0.2.0')
    self.assertIsNone(report.backupPath)
    self.assertEqual((targetRoot / '.template-version').read_bytes(), b'0.2.0\n')
    self.assertTrue((targetRoot / 'MEMORY.md').is_file())
    self.assertTrue((targetRoot / 'RULES.md').is_file())
    self.assertTrue((targetRoot / 'config.json').is_file())
    self.assertTrue((targetRoot / 'spaces' / '学业.md').is_file())
    self.assertTrue((targetRoot / 'ARCHIVE').is_dir())
    self.assertFalse((targetRoot / 'check-memory.ps1').exists())
    self.assertIn(targetRoot / '.template-version', report.createdPaths)

    before = self.snapshotTree(targetRoot)
    with self.assertRaises(InstallError):
      installTemplate(self.repositoryRoot, targetRoot)
    self.assertEqual(self.snapshotTree(targetRoot), before)

  def testUpdatePreservesExistingBytesAndAddsMissingTemplatePaths(self) -> None:
    """功能：验证更新保留所有用户内容，只补缺失模板并备份版本元数据。

    入参：无。
    返回值：无，通过逐文件字节、补入路径和备份报告断言结果。
    边界情况：覆盖热记忆、规则、配置、分类、归档和自定义目录多种已有内容。
    """
    targetRoot = self.tempRoot / 'installed-memory'
    installTemplate(self.repositoryRoot, targetRoot)
    customBytes = {
      'MEMORY.md': b'custom hot memory\r\n',
      'RULES.md': b'custom rules\n',
      'config.json': b'{"custom": true}\n',
      'spaces/学业.md': b'custom category\n',
      'ARCHIVE/2025-01-history.md': b'custom archive\n',
      'custom/note.bin': b'\x00\x01custom',
    }
    for relativePath, content in customBytes.items():
      filePath = targetRoot / relativePath
      filePath.parent.mkdir(parents=True, exist_ok=True)
      filePath.write_bytes(content)
    missingCategoryPath = targetRoot / 'spaces' / '任务要求.md'
    missingCategoryPath.unlink()
    oldVersionBytes = b'0.1.0\r\n'
    (targetRoot / '.template-version').write_bytes(oldVersionBytes)
    (self.repositoryRoot / 'memory' / 'new-template.txt').write_bytes(b'new template\n')

    report = installTemplate(self.repositoryRoot, targetRoot, update=True)

    for relativePath, content in customBytes.items():
      self.assertEqual((targetRoot / relativePath).read_bytes(), content)
    self.assertEqual(
      missingCategoryPath.read_bytes(),
      (self.repositoryRoot / 'memory' / 'spaces' / '任务要求.md').read_bytes(),
    )
    self.assertEqual((targetRoot / 'new-template.txt').read_bytes(), b'new template\n')
    self.assertEqual((targetRoot / '.template-version').read_bytes(), b'0.2.0\n')
    self.assertIsNotNone(report.backupPath)
    assert report.backupPath is not None
    self.assertEqual(report.backupPath.read_bytes(), oldVersionBytes)
    self.assertIn(missingCategoryPath, report.createdPaths)
    self.assertIn(targetRoot / 'new-template.txt', report.createdPaths)
    self.assertNotIn(targetRoot / 'MEMORY.md', report.createdPaths)

  def testFailedUpdateRollsBackAddedPathsAndRestoresMetadata(self) -> None:
    """功能：验证更新中途写入失败会撤销新增路径并恢复旧版本元数据。

    入参：无。
    返回值：无，通过失败前后快照、备份保留和错误上下文断言结果。
    边界情况：只注入第二个新增模板文件的复制失败，真实备份与首个复制仍执行。
    """
    targetRoot = self.tempRoot / 'rollback-target'
    installTemplate(self.repositoryRoot, targetRoot)
    oldVersionBytes = b'old-version-with-custom-bytes\r\n'
    (targetRoot / '.template-version').write_bytes(oldVersionBytes)
    (self.repositoryRoot / 'memory' / 'add-a.txt').write_bytes(b'first\n')
    (self.repositoryRoot / 'memory' / 'add-b.txt').write_bytes(b'second\n')
    before = self.snapshotTree(targetRoot)
    realCopy = shutil.copy2

    def failingCopy(sourcePath: str | os.PathLike[str], destinationPath: str | os.PathLike[str], *args: object, **kwargs: object) -> str:
      """功能：仅为指定新增模板文件注入一次磁盘复制失败。

      入参：sourcePath、destinationPath 为复制路径，args 与 kwargs 透传给真实 copy2。
      返回值：未命中故障文件时返回真实 copy2 的目标路径字符串。
      边界情况：源文件名为 add-b.txt 时固定抛出 OSError。
      """
      if Path(sourcePath).name == 'add-b.txt':
        raise OSError('injected copy failure')
      return realCopy(sourcePath, destinationPath, *args, **kwargs)

    with mock.patch('codex_memory.installer.shutil.copy2', side_effect=failingCopy):
      with self.assertRaises(InstallError) as context:
        installTemplate(self.repositoryRoot, targetRoot, update=True)

    backupPaths = sorted(targetRoot.glob('.template-version.backup-*'))
    self.assertEqual(len(backupPaths), 1)
    self.assertEqual(backupPaths[0].read_bytes(), oldVersionBytes)
    self.assertIn(str(backupPaths[0]), str(context.exception))
    self.assertFalse((targetRoot / 'add-a.txt').exists())
    self.assertFalse((targetRoot / 'add-b.txt').exists())
    self.assertEqual((targetRoot / '.template-version').read_bytes(), oldVersionBytes)
    afterWithoutBackup = self.snapshotTree(targetRoot)
    afterWithoutBackup.pop(backupPaths[0].name)
    self.assertEqual(afterWithoutBackup, before)

  def testUpdateNeverOverwritesFileCreatedDuringPlacement(self) -> None:
    """功能：验证更新落位竞争窗口中出现的用户文件绝不被模板覆盖。

    入参：无。
    返回值：无，通过 InstallError 和并发用户文件字节断言结果。
    边界情况：只在最终不可覆盖链接操作前创建同名文件，其他硬链接正常执行。
    """
    targetRoot = self.tempRoot / 'concurrent-file-target'
    installTemplate(self.repositoryRoot, targetRoot)
    sourcePath = self.repositoryRoot / 'memory' / 'concurrent.txt'
    sourcePath.write_bytes(b'template bytes\n')
    destinationPath = targetRoot / 'concurrent.txt'
    userBytes = b'user-created-during-placement\n'
    realLink = os.link

    def racingLink(
      sourceLinkPath: str | os.PathLike[str],
      destinationLinkPath: str | os.PathLike[str],
      *args: object,
      **kwargs: object,
    ) -> None:
      """功能：在模板文件最终落位前模拟用户创建同名目标文件。

      入参：sourceLinkPath、destinationLinkPath 为硬链接路径，其余参数透传真实 os.link。
      返回值：无，真实链接成功时正常返回。
      边界情况：仅目标是 concurrent.txt 时注入一次竞争写入。
      """
      if Path(destinationLinkPath) == destinationPath and not destinationPath.exists():
        destinationPath.write_bytes(userBytes)
      realLink(sourceLinkPath, destinationLinkPath, *args, **kwargs)

    with mock.patch('codex_memory.installer.os.link', side_effect=racingLink):
      with self.assertRaises(InstallError):
        installTemplate(self.repositoryRoot, targetRoot, update=True)

    self.assertEqual(destinationPath.read_bytes(), userBytes)

  def testFirstInstallDoesNotUseReplacingRenameForTargetRoot(self) -> None:
    """功能：验证首次安装通过独占目录创建保留目标，而不调用可覆盖式 os.replace 落位根目录。

    入参：无。
    返回值：无，通过成功安装和目标根替换调用零命中断言结果。
    边界情况：允许版本元数据等目标内部原子替换，不允许以目标根作为 replace 终点。
    """
    targetRoot = self.tempRoot / 'exclusive-install-target'
    realReplace = os.replace
    replacedRoot = False

    def trackingReplace(
      sourcePath: str | os.PathLike[str],
      destinationPath: str | os.PathLike[str],
    ) -> None:
      """功能：记录并拒绝以安装目标根为终点的替换式重命名。

      入参：sourcePath 为源路径，destinationPath 为替换目标。
      返回值：无，非目标根操作透传真实 os.replace。
      边界情况：目标根命中时抛出 AssertionError 使旧实现产生明确 RED。
      """
      nonlocal replacedRoot
      if Path(destinationPath) == targetRoot:
        replacedRoot = True
        raise AssertionError('target root must use exclusive mkdir')
      realReplace(sourcePath, destinationPath)

    with mock.patch('codex_memory.installer.os.replace', side_effect=trackingReplace):
      installTemplate(self.repositoryRoot, targetRoot)

    self.assertFalse(replacedRoot)
    self.assertTrue((targetRoot / 'MEMORY.md').is_file())

  @unittest.skipUnless(os.name == 'nt', 'Windows 专属安全目录重命名语义')
  def testWindowsFirstInstallKeepsConcurrentTargetAndCleansStaging(self) -> None:
    """功能：验证 Windows 首次安装通过同级 staging 安全重命名且不覆盖竞争目标。

    入参：无。
    返回值：无，通过用户哨兵保留和 staging 清理断言结果。
    边界情况：在最终目录重命名前创建非空同名目标，真实 Windows rename 必须失败。
    """
    targetRoot = self.tempRoot / 'windows-race-target'
    sentinelBytes = b'concurrent user directory\n'
    realRename = os.rename

    def racingRename(
      sourcePath: str | os.PathLike[str],
      destinationPath: str | os.PathLike[str],
    ) -> None:
      """功能：在 staging 最终重命名前模拟用户占用安装目标。

      入参：sourcePath 为 staging，destinationPath 为安装目标。
      返回值：无，注入竞争目录后调用真实 os.rename。
      边界情况：只对目标根注入一次，其他重命名直接透传。
      """
      if Path(destinationPath) == targetRoot and not targetRoot.exists():
        targetRoot.mkdir()
        (targetRoot / 'sentinel.txt').write_bytes(sentinelBytes)
      realRename(sourcePath, destinationPath)

    with mock.patch('codex_memory.installer.os.rename', side_effect=racingRename):
      with self.assertRaises(InstallError):
        installTemplate(self.repositoryRoot, targetRoot)

    self.assertEqual((targetRoot / 'sentinel.txt').read_bytes(), sentinelBytes)
    self.assertFalse(any(self.tempRoot.glob('.windows-race-target.install-*')))

  def testExclusiveFallbackWorksWhenHardLinksAreUnsupported(self) -> None:
    """功能：验证文件系统不支持硬链接时使用 O_EXCL 回退且仍完整安装更新。

    入参：无。
    返回值：无，通过模板字节、报告和版本更新断言结果。
    边界情况：所有 os.link 均抛出 ENOTSUP，覆盖首次安装、备份和新增文件。
    """
    targetRoot = self.tempRoot / 'no-hardlink-target'
    with mock.patch(
      'codex_memory.installer.os.link',
      side_effect=OSError(errno.ENOTSUP, 'hard links unsupported'),
    ):
      installTemplate(self.repositoryRoot, targetRoot)
      (self.repositoryRoot / 'memory' / 'fallback.txt').write_bytes(b'fallback bytes\n')
      report = installTemplate(self.repositoryRoot, targetRoot, update=True)

    self.assertEqual((targetRoot / 'fallback.txt').read_bytes(), b'fallback bytes\n')
    self.assertEqual((targetRoot / '.template-version').read_bytes(), b'0.2.0\n')
    self.assertIsNotNone(report.backupPath)

  def testFallbackNeverOverwritesConcurrentDestination(self) -> None:
    """功能：验证硬链接不可用时 O_EXCL 回退也不覆盖竞争窗口出现的用户文件。

    入参：无。
    返回值：无，通过 InstallError 和用户字节保留断言结果。
    边界情况：在回退打开最终目标前创建同名文件，其他 os.open 调用原样透传。
    """
    targetRoot = self.tempRoot / 'fallback-race-target'
    installTemplate(self.repositoryRoot, targetRoot)
    (self.repositoryRoot / 'memory' / 'fallback-race.txt').write_bytes(b'template\n')
    destinationPath = targetRoot / 'fallback-race.txt'
    userBytes = b'user wins fallback race\n'
    realOpen = os.open

    def racingOpen(
      path: str | os.PathLike[str],
      flags: int,
      mode: int = 0o777,
    ) -> int:
      """功能：在 O_EXCL 最终目标打开前模拟用户创建同名文件。

      入参：path、flags、mode 为 os.open 标准参数。
      返回值：非目标调用返回真实文件描述符，目标调用因已存在由真实 os.open 抛错。
      边界情况：仅最终 fallback-race.txt 触发竞争写入。
      """
      if Path(path) == destinationPath and not destinationPath.exists():
        destinationPath.write_bytes(userBytes)
      return realOpen(path, flags, mode)

    with mock.patch(
      'codex_memory.installer.os.link',
      side_effect=OSError(errno.ENOTSUP, 'hard links unsupported'),
    ), mock.patch('codex_memory.installer.os.open', side_effect=racingOpen):
      with self.assertRaises(InstallError):
        installTemplate(self.repositoryRoot, targetRoot, update=True)

    self.assertEqual(destinationPath.read_bytes(), userBytes)

  def testFilePlacementTrackingFailureCannotLeaveUnownedPath(self) -> None:
    """功能：验证旧式落位后身份捕获故障不会留下不可追踪的新文件。

    入参：无。
    返回值：无，通过后续故障回滚后两个新增文件均不存在断言结果。
    边界情况：对首个最终文件注入 `_captureOwnedPath` 故障，第二个复制再触发 OSError。
    """
    targetRoot = self.tempRoot / 'placement-tracking-target'
    installTemplate(self.repositoryRoot, targetRoot)
    firstSource = self.repositoryRoot / 'memory' / 'tracking-a.txt'
    secondSource = self.repositoryRoot / 'memory' / 'tracking-b.txt'
    firstSource.write_bytes(b'first\n')
    secondSource.write_bytes(b'second\n')
    realCapture = installerModule._captureOwnedPath
    realCopy = shutil.copy2

    def failingCapture(path: Path) -> object:
      """功能：模拟最终文件落位后再捕获身份失败的旧实现窗口。

      入参：path 为待捕获路径。
      返回值：非目标路径返回真实所有权记录。
      边界情况：只对 tracking-a.txt 抛出 OSError。
      """
      if path.name == 'tracking-a.txt':
        raise OSError('injected post-placement capture failure')
      return realCapture(path)

    def failSecondCopy(
      sourcePath: str | os.PathLike[str],
      destinationPath: str | os.PathLike[str],
      *args: object,
      **kwargs: object,
    ) -> str:
      """功能：在第二个新增模板复制时注入故障以触发完整回滚。

      入参：sourcePath、destinationPath 为复制路径，其余参数透传真实 copy2。
      返回值：非故障源返回真实 copy2 结果。
      边界情况：tracking-b.txt 固定抛出 OSError。
      """
      if Path(sourcePath).name == 'tracking-b.txt':
        raise OSError('injected second copy failure')
      return realCopy(sourcePath, destinationPath, *args, **kwargs)

    with mock.patch(
      'codex_memory.installer._captureOwnedPath',
      side_effect=failingCapture,
    ), mock.patch(
      'codex_memory.installer.shutil.copy2',
      side_effect=failSecondCopy,
    ):
      with self.assertRaises(InstallError):
        installTemplate(self.repositoryRoot, targetRoot, update=True)

    self.assertFalse((targetRoot / 'tracking-a.txt').exists())
    self.assertFalse((targetRoot / 'tracking-b.txt').exists())

  def testVersionTrackingFailureCannotPreventMetadataRecovery(self) -> None:
    """功能：验证旧式版本替换后身份捕获故障不会阻止恢复旧元数据。

    入参：无。
    返回值：无，通过后续模板故障后版本原始字节恢复断言结果。
    边界情况：仅对 `.template-version` 注入捕获故障，新增模板复制随后失败。
    """
    targetRoot = self.tempRoot / 'version-tracking-target'
    installTemplate(self.repositoryRoot, targetRoot)
    versionPath = targetRoot / '.template-version'
    oldVersionBytes = b'old tracked version\r\n'
    versionPath.write_bytes(oldVersionBytes)
    triggerPath = self.repositoryRoot / 'memory' / 'version-trigger.txt'
    triggerPath.write_bytes(b'trigger\n')
    realCapture = installerModule._captureOwnedPath
    realCopy = shutil.copy2

    def failingVersionCapture(path: Path) -> object:
      """功能：模拟版本替换成功后身份捕获失败的旧实现窗口。

      入参：path 为待捕获路径。
      返回值：非版本路径返回真实所有权记录。
      边界情况：`.template-version` 固定抛出 OSError。
      """
      if path == versionPath:
        raise OSError('injected version capture failure')
      return realCapture(path)

    def failTriggerCopy(
      sourcePath: str | os.PathLike[str],
      destinationPath: str | os.PathLike[str],
      *args: object,
      **kwargs: object,
    ) -> str:
      """功能：在版本替换之后为指定新增模板注入复制故障。

      入参：sourcePath、destinationPath 为复制路径，其余参数透传真实 copy2。
      返回值：非触发源返回真实 copy2 结果。
      边界情况：version-trigger.txt 固定抛出 OSError。
      """
      if Path(sourcePath).name == 'version-trigger.txt':
        raise OSError('injected failure after version placement')
      return realCopy(sourcePath, destinationPath, *args, **kwargs)

    with mock.patch(
      'codex_memory.installer._captureOwnedPath',
      side_effect=failingVersionCapture,
    ), mock.patch(
      'codex_memory.installer.shutil.copy2',
      side_effect=failTriggerCopy,
    ):
      with self.assertRaises(InstallError):
        installTemplate(self.repositoryRoot, targetRoot, update=True)

    self.assertEqual(versionPath.read_bytes(), oldVersionBytes)

  def testRollbackKeepsConcurrentReplacementAtCreatedPath(self) -> None:
    """功能：验证回滚只删除仍由安装器持有的对象，不删除用户并发替换内容。

    入参：无。
    返回值：无，通过失败后替换文件仍存在且字节不变断言结果。
    边界情况：首个新增文件落位后被替换，第二个文件复制时触发故障。
    """
    targetRoot = self.tempRoot / 'identity-rollback-target'
    installTemplate(self.repositoryRoot, targetRoot)
    firstSource = self.repositoryRoot / 'memory' / 'identity-a.txt'
    secondSource = self.repositoryRoot / 'memory' / 'identity-b.txt'
    firstSource.write_bytes(b'template first\n')
    secondSource.write_bytes(b'template second\n')
    replacementBytes = b'user replacement\n'
    realCopy = shutil.copy2

    def replacingFailure(
      sourcePath: str | os.PathLike[str],
      destinationPath: str | os.PathLike[str],
      *args: object,
      **kwargs: object,
    ) -> str:
      """功能：在第二个文件复制失败前替换首个已落位目标。

      入参：sourcePath、destinationPath 为复制路径，其余参数透传真实 copy2。
      返回值：非故障源返回真实 copy2 的结果。
      边界情况：命中 identity-b.txt 时用 os.replace 生成不同身份的用户文件后抛错。
      """
      if Path(sourcePath).name == 'identity-b.txt':
        replacementPath = targetRoot / 'replacement.tmp'
        replacementPath.write_bytes(replacementBytes)
        os.replace(replacementPath, targetRoot / 'identity-a.txt')
        raise OSError('injected failure after replacement')
      return realCopy(sourcePath, destinationPath, *args, **kwargs)

    with mock.patch('codex_memory.installer.shutil.copy2', side_effect=replacingFailure):
      with self.assertRaises(InstallError):
        installTemplate(self.repositoryRoot, targetRoot, update=True)

    self.assertEqual((targetRoot / 'identity-a.txt').read_bytes(), replacementBytes)
    self.assertFalse((targetRoot / 'identity-b.txt').exists())

  def testFailureAfterMetadataReplacementRestoresOriginalBytes(self) -> None:
    """功能：验证版本元数据已经替换后发生故障仍恢复原始字节。

    入参：无。
    返回值：无，通过故障注入时观察的新版本和最终旧版本字节断言结果。
    边界情况：在第一个缺失模板文件写入前抛出 PermissionError。
    """
    targetRoot = self.tempRoot / 'post-metadata-failure-target'
    installTemplate(self.repositoryRoot, targetRoot)
    oldVersionBytes = b'0.1.0-custom\r\n'
    versionPath = targetRoot / '.template-version'
    versionPath.write_bytes(oldVersionBytes)
    (self.repositoryRoot / 'memory' / 'permission.txt').write_bytes(b'template\n')
    observedVersions: list[bytes] = []
    realCopyAtomic = installerModule._copyFileAtomically

    def failAfterMetadata(sourcePath: Path, destinationPath: Path) -> object:
      """功能：记录故障时版本元数据并模拟目标不可写。

      入参：sourcePath 和 destinationPath 为待复制模板及目标路径。
      返回值：无，固定抛出 PermissionError。
      边界情况：只用于验证故障发生在版本替换之后。
      """
      if sourcePath.name == 'permission.txt':
        observedVersions.append(versionPath.read_bytes())
        raise PermissionError('injected unwritable target')
      return realCopyAtomic(sourcePath, destinationPath)

    with mock.patch('codex_memory.installer._copyFileAtomically', side_effect=failAfterMetadata):
      with self.assertRaises(InstallError):
        installTemplate(self.repositoryRoot, targetRoot, update=True)

    self.assertEqual(observedVersions, [b'0.2.0\n'])
    self.assertEqual(versionPath.read_bytes(), oldVersionBytes)

  def testBackupNameCollisionDoesNotOverwriteExistingFile(self) -> None:
    """功能：验证备份候选名冲突时保留原文件并选择新的不可覆盖名称。

    入参：无。
    返回值：无，通过预占文件字节和最终报告路径断言结果。
    边界情况：固定 UUID 迫使实现使用带递增后缀的候选名重试。
    """
    targetRoot = self.tempRoot / 'backup-collision-target'
    installTemplate(self.repositoryRoot, targetRoot)
    occupiedPath = targetRoot / '.template-version.backup-collision'
    occupiedBytes = b'do not overwrite this backup\n'
    occupiedPath.write_bytes(occupiedBytes)

    with mock.patch(
      'codex_memory.installer.uuid.uuid4',
      return_value=SimpleNamespace(hex='collision'),
    ):
      report = installTemplate(self.repositoryRoot, targetRoot, update=True)

    self.assertEqual(occupiedPath.read_bytes(), occupiedBytes)
    self.assertIsNotNone(report.backupPath)
    self.assertNotEqual(report.backupPath, occupiedPath)
    assert report.backupPath is not None
    self.assertEqual(report.backupPath.read_bytes(), b'0.2.0\n')

  def testRestoreFailureIsReportedWithBackupLocation(self) -> None:
    """功能：验证元数据恢复失败不会被吞掉，并明确报告失败状态与备份位置。

    入参：无。
    返回值：无，通过错误消息和备份文件仍存在断言结果。
    边界情况：复制模板故障后，再单独注入恢复过程 OSError。
    """
    targetRoot = self.tempRoot / 'restore-failure-target'
    installTemplate(self.repositoryRoot, targetRoot)
    (self.repositoryRoot / 'memory' / 'restore-trigger.txt').write_bytes(b'template\n')
    realCopyAtomic = installerModule._copyFileAtomically

    def failTemplateCopy(sourcePath: Path, destinationPath: Path) -> object:
      """功能：只让新增模板复制失败，保留元数据备份创建的真实行为。

      入参：sourcePath 和 destinationPath 为复制源和目标。
      返回值：备份复制返回真实结果；模板文件固定抛出 PermissionError。
      边界情况：通过源文件名区分备份与新增模板。
      """
      if sourcePath.name == 'restore-trigger.txt':
        raise PermissionError('copy failure')
      return realCopyAtomic(sourcePath, destinationPath)

    with mock.patch(
      'codex_memory.installer._copyFileAtomically',
      side_effect=failTemplateCopy,
    ), mock.patch(
      'codex_memory.installer._restoreMetadata',
      side_effect=OSError('restore failure'),
    ):
      with self.assertRaises(InstallError) as context:
        installTemplate(self.repositoryRoot, targetRoot, update=True)

    backupPaths = list(targetRoot.glob('.template-version.backup-*'))
    self.assertEqual(len(backupPaths), 1)
    self.assertIn('恢复失败', str(context.exception))
    self.assertIn(str(backupPaths[0]), str(context.exception))

  def testInjectedUnwritableTargetLeavesExistingBytesUntouched(self) -> None:
    """功能：验证目标不可写故障转为 InstallError 且现有用户字节不变。

    入参：无。
    返回值：无，通过目标快照和错误类型断言结果。
    边界情况：以 PermissionError 注入替代跨平台不一致的 chmod 权限模拟。
    """
    targetRoot = self.tempRoot / 'unwritable-target'
    installTemplate(self.repositoryRoot, targetRoot)
    (self.repositoryRoot / 'memory' / 'blocked.txt').write_bytes(b'template\n')
    before = self.snapshotTree(targetRoot)
    realCopyAtomic = installerModule._copyFileAtomically

    def failBlockedCopy(sourcePath: Path, destinationPath: Path) -> object:
      """功能：只为 blocked.txt 注入不可写故障，允许版本备份真实创建。

      入参：sourcePath 和 destinationPath 为复制源和目标。
      返回值：备份复制返回真实结果；blocked.txt 固定抛出 PermissionError。
      边界情况：不依赖 chmod 和当前操作系统权限模型。
      """
      if sourcePath.name == 'blocked.txt':
        raise PermissionError('injected unwritable target')
      return realCopyAtomic(sourcePath, destinationPath)

    with mock.patch(
      'codex_memory.installer._copyFileAtomically',
      side_effect=failBlockedCopy,
    ):
      with self.assertRaises(InstallError):
        installTemplate(self.repositoryRoot, targetRoot, update=True)

    after = self.snapshotTree(targetRoot)
    backupName = next(name for name in after if name.startswith('.template-version.backup-'))
    after.pop(backupName)
    self.assertEqual(after, before)

  def testRejectsSourceDirectoryLinkBeforeCreatingTarget(self) -> None:
    """功能：验证源模板含目录链接时在任何目标写入前拒绝安装。

    入参：无。
    返回值：无，通过目标不存在和外部哨兵字节不变断言结果。
    边界情况：Windows 无符号链接权限时使用 junction，均不得遍历外部目录。
    """
    outsideRoot = self.tempRoot / 'outside-source'
    outsideRoot.mkdir()
    sentinelPath = outsideRoot / 'sentinel.md'
    sentinelBytes = b'outside source sentinel\n'
    sentinelPath.write_bytes(sentinelBytes)
    self.replaceWithDirectoryLink(self.repositoryRoot / 'memory' / 'spaces', outsideRoot)
    targetRoot = self.tempRoot / 'must-not-exist'

    with self.assertRaises(InstallError):
      installTemplate(self.repositoryRoot, targetRoot)

    self.assertFalse(targetRoot.exists())
    self.assertEqual(sentinelPath.read_bytes(), sentinelBytes)

  def testRejectsSourceFileLinkBeforeCreatingTarget(self) -> None:
    """功能：验证源模板普通文件链接在创建目标前被拒绝且外部字节不变。

    入参：无。
    返回值：无，通过目标不存在和外部哨兵字节断言结果。
    边界情况：平台不允许文件符号链接时跳过。
    """
    sentinelPath = self.tempRoot / 'outside-source-file.md'
    sentinelBytes = b'outside source file\n'
    sentinelPath.write_bytes(sentinelBytes)
    self.replaceWithFileLink(self.repositoryRoot / 'memory' / 'MEMORY.md', sentinelPath)
    targetRoot = self.tempRoot / 'source-file-link-target'

    with self.assertRaises(InstallError):
      installTemplate(self.repositoryRoot, targetRoot)

    self.assertFalse(targetRoot.exists())
    self.assertEqual(sentinelPath.read_bytes(), sentinelBytes)

  def testRejectsTargetDirectoryLinkBeforeUpdateMutation(self) -> None:
    """功能：验证目标树含目录链接时在备份或补入前拒绝更新。

    入参：无。
    返回值：无，通过完整目标快照和外部哨兵字节不变断言结果。
    边界情况：链接位于模板已有目录位置，避免实现只扫描新增文件而漏检。
    """
    targetRoot = self.tempRoot / 'linked-target'
    installTemplate(self.repositoryRoot, targetRoot)
    outsideRoot = self.tempRoot / 'outside-target'
    outsideRoot.mkdir()
    sentinelPath = outsideRoot / 'sentinel.md'
    sentinelBytes = b'outside target sentinel\n'
    sentinelPath.write_bytes(sentinelBytes)
    self.replaceWithDirectoryLink(targetRoot / 'spaces', outsideRoot)
    before = self.snapshotTree(targetRoot)

    with self.assertRaises(InstallError):
      installTemplate(self.repositoryRoot, targetRoot, update=True)

    self.assertEqual(self.snapshotTree(targetRoot), before)
    self.assertEqual(sentinelPath.read_bytes(), sentinelBytes)
    self.assertFalse(any(targetRoot.glob('.template-version.backup-*')))

  def testRejectsTargetFileLinkBeforeUpdateMutation(self) -> None:
    """功能：验证目标普通文件链接在备份或写入前被拒绝且外部字节不变。

    入参：无。
    返回值：无，通过目标快照、无备份和外部哨兵字节断言结果。
    边界情况：平台不允许文件符号链接时跳过。
    """
    targetRoot = self.tempRoot / 'target-file-link'
    installTemplate(self.repositoryRoot, targetRoot)
    sentinelPath = self.tempRoot / 'outside-target-file.md'
    sentinelBytes = b'outside target file\n'
    sentinelPath.write_bytes(sentinelBytes)
    self.replaceWithFileLink(targetRoot / 'MEMORY.md', sentinelPath)
    before = self.snapshotTree(targetRoot)

    with self.assertRaises(InstallError):
      installTemplate(self.repositoryRoot, targetRoot, update=True)

    self.assertEqual(self.snapshotTree(targetRoot), before)
    self.assertEqual(sentinelPath.read_bytes(), sentinelBytes)
    self.assertFalse(any(targetRoot.glob('.template-version.backup-*')))

  def testRejectsOverlappingSourceAndTargetWithoutMutation(self) -> None:
    """功能：验证目标与模板源树重叠时拒绝操作，避免更新反向污染仓库模板。

    入参：无。
    返回值：无，通过源模板完整快照不变断言结果。
    边界情况：覆盖目标恰好等于 `repositoryRoot/memory` 的直接重叠情况。
    """
    sourceRoot = self.repositoryRoot / 'memory'
    before = self.snapshotTree(sourceRoot)

    with self.assertRaises(InstallError):
      installTemplate(self.repositoryRoot, sourceRoot, update=True)

    self.assertEqual(self.snapshotTree(sourceRoot), before)


if __name__ == '__main__':
  unittest.main()
