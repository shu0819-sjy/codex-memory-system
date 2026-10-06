"""命令行接口与只读接入说明的行为测试。"""

import contextlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from codex_memory import cli
from codex_memory.checker import CheckResult


class CliTestCase(unittest.TestCase):
  """验证公开命令的路径、交互、退出状态和输出安全性。"""

  def setUp(self) -> None:
    """功能：建立隔离的临时安装目录和输出缓冲。

    入参：无。
    返回值：无。
    边界情况：不触碰真实用户目录。
    """
    self.tempDirectory = tempfile.TemporaryDirectory()
    self.tempRoot = Path(self.tempDirectory.name)
    self.output = io.StringIO()
    self.errors = io.StringIO()

  def tearDown(self) -> None:
    """功能：清理测试创建的临时文件。

    入参：无。
    返回值：无。
    边界情况：测试断言失败仍清理。
    """
    self.tempDirectory.cleanup()

  def runCli(self, arguments: list[str]) -> int:
    """功能：收集一次真实 CLI 命令的标准输出和错误。

    入参：arguments 为不经 shell 插值的参数列表。
    返回值：命令退出状态。
    边界情况：输出只保留在本测试实例的缓冲区。
    """
    with contextlib.redirect_stdout(self.output), contextlib.redirect_stderr(self.errors):
      return cli.main(arguments)

  def testInstallExplicitRootAndUpdatePreservesPersonalData(self) -> None:
    """功能：首次安装和更新使用显式目录并保留用户数据。

    入参：无。
    返回值：无。
    边界情况：目录含空格和中文，已有正文在更新后仍不变。
    """
    targetRoot = self.tempRoot / '中文 memory folder'
    self.assertEqual(self.runCli(['install', '--root', str(targetRoot), '--non-interactive']), 0)
    self.assertTrue((targetRoot / 'config.json').is_file())
    memoryPath = targetRoot / 'MEMORY.md'
    memoryPath.write_text('私人文本\n', encoding='utf-8')
    self.assertEqual(self.runCli(['install', '--root', str(targetRoot), '--update', '--non-interactive']), 0)
    self.assertEqual(memoryPath.read_text(encoding='utf-8'), '私人文本\n')
    self.assertEqual(self.runCli(['install', '--root', str(targetRoot), '--non-interactive']), 2)

  def testInstallUsesDefaultHomeWithoutTerminal(self) -> None:
    """功能：非交互安装使用家目录下默认位置。

    入参：无。
    返回值：无。
    边界情况：模拟非 TTY，即使未传非交互标记也不询问。
    """
    with mock.patch('codex_memory.cli.Path.home', return_value=self.tempRoot):
      with mock.patch('codex_memory.cli.sys.stdin.isatty', return_value=False):
        self.assertEqual(self.runCli(['install']), 0)
    self.assertTrue((self.tempRoot / '.codex-memory' / 'MEMORY.md').is_file())

  def testInteractiveInstallDeclinedWithoutWrites(self) -> None:
    """功能：交互安装拒绝选择时不创建目标。

    入参：无。
    返回值：无。
    边界情况：仅双向 TTY 时询问，输入拒绝不产生目录。
    """
    targetRoot = self.tempRoot / 'declined'
    with mock.patch('codex_memory.cli.sys.stdin.isatty', return_value=True):
      with mock.patch.object(self.output, 'isatty', return_value=True):
        with mock.patch('builtins.input', return_value='n'):
          self.assertEqual(self.runCli(['install', '--root', str(targetRoot)]), 2)
    self.assertFalse(targetRoot.exists())

  def testNonInteractiveFlagDisablesPrompt(self) -> None:
    """功能：显式非交互模式即使双向 TTY 也不询问。

    入参：无。
    返回值：无。
    边界情况：传入的显式目标直接安装。
    """
    targetRoot = self.tempRoot / 'scripted'
    with mock.patch('codex_memory.cli.sys.stdin.isatty', return_value=True):
      with mock.patch.object(self.output, 'isatty', return_value=True):
        with mock.patch('builtins.input', side_effect=AssertionError('不应交互')):
          self.assertEqual(self.runCli(['install', '--root', str(targetRoot), '--non-interactive']), 0)
    self.assertTrue(targetRoot.is_dir())

  def testInteractiveInstallAcceptsChosenRoot(self) -> None:
    """功能：终端用户输入安装路径后在该路径建立模板。

    入参：无。
    返回值：无。
    边界情况：手动选择的目录含空格与单引号。
    """
    targetRoot = self.tempRoot / "my friend's memory"
    with mock.patch('codex_memory.cli.sys.stdin.isatty', return_value=True):
      with mock.patch.object(self.output, 'isatty', return_value=True):
        with mock.patch('builtins.input', return_value=str(targetRoot)):
          self.assertEqual(self.runCli(['install']), 0)
    self.assertTrue((targetRoot / 'MEMORY.md').is_file())

  def testCheckSeverityDeterminesExitWithoutLeakingContent(self) -> None:
    """功能：检查通过或警告返回零，失败返回一且不回显正文。

    入参：无。
    返回值：无。
    边界情况：恶意结果携带绝对路径和私人正文也仅显示固定诊断。
    """
    targetRoot = self.tempRoot / 'check'
    targetRoot.mkdir()
    for severity, expectedCode in (('WARN', 0), ('FAIL', 1)):
      self.output = io.StringIO()
      with mock.patch('codex_memory.cli.checkMemory', return_value=[
        CheckResult(severity, 'PRIVATE_SECRET', 'C:/PRIVATE_SECRET', 9),
      ]):
        self.assertEqual(self.runCli(['check', '--root', str(targetRoot)]), expectedCode)
      self.assertIn(severity, self.output.getvalue())
      self.assertNotIn('PRIVATE_SECRET', self.output.getvalue())

  def testInvalidConfigIsStatusTwoWithoutEcho(self) -> None:
    """功能：配置错误返回状态二且不显示原始内容。

    入参：无。
    返回值：无。
    边界情况：配置故意写入敏感字节时输出必须脱敏。
    """
    targetRoot = self.tempRoot / 'invalid'
    targetRoot.mkdir()
    (targetRoot / 'config.json').write_text('{"PRIVATE_SECRET":', encoding='utf-8')
    self.assertEqual(self.runCli(['check', '--root', str(targetRoot)]), 2)
    self.assertNotIn('PRIVATE_SECRET', self.output.getvalue() + self.errors.getvalue())

  def testControlCharactersAreRejectedBeforeInstallAndHiddenInDiagnostics(self) -> None:
    """功能：拒绝安装目录中的 DEL、C1 控制字符并隐藏诊断路径。

    入参：无。
    返回值：无。
    边界情况：两种控制字符均不得出现于输出或新增目录。
    """
    for character in ('\x7f', '\x85'):
      targetRoot = self.tempRoot / f'private{character}path'
      self.output = io.StringIO()
      self.errors = io.StringIO()
      self.assertEqual(self.runCli(['install', '--root', str(targetRoot), '--non-interactive']), 2)
      self.assertFalse(targetRoot.exists())
      self.assertNotIn(character, self.output.getvalue() + self.errors.getvalue())
      self.assertEqual(self.runCli(['instructions', '--root', str(targetRoot)]), 2)
      self.assertNotIn(character, self.output.getvalue() + self.errors.getvalue())
    safeRoot = self.tempRoot / 'safe'
    safeRoot.mkdir()
    with mock.patch('codex_memory.cli.checkMemory', return_value=[
      CheckResult('FAIL', '检测到隐私风险', 'spaces/private\x85path.md', 4),
    ]):
      self.assertEqual(self.runCli(['check', '--root', str(safeRoot)]), 1)
    self.assertNotIn('\x85', self.output.getvalue())

  def testInstructionsDoNotWriteFiles(self) -> None:
    """功能：接入说明输出可复制的命令而不改用户文件。

    入参：无。
    返回值：无。
    边界情况：自定义目录包含空格，不应触发安装或写 AGENTS。
    """
    targetRoot = self.tempRoot / '中文 custom root'
    agentPath = self.tempRoot / 'AGENTS.md'
    agentPath.write_text('original', encoding='utf-8')
    self.assertEqual(self.runCli(['instructions', '--root', str(targetRoot)]), 0)
    self.assertEqual(agentPath.read_text(encoding='utf-8'), 'original')
    self.assertFalse(targetRoot.exists())
    self.assertIn(json.dumps(str(targetRoot), ensure_ascii=False), self.output.getvalue())
    self.assertIn('codex_memory', self.output.getvalue())
    self.assertIn('sh ./codex-memory check', self.output.getvalue())
    self.assertNotIn('check-memory.ps1', self.output.getvalue())
    self.assertIn(f"check --root '{targetRoot}'", self.output.getvalue())

  def testUnknownCommandAndHelp(self) -> None:
    """功能：帮助正常退出而无效命令按参数错误返回二。

    入参：无。
    返回值：无。
    边界情况：argparse 的 SystemExit 在入口内转换为数值状态。
    """
    self.assertEqual(self.runCli(['--help']), 0)
    self.assertEqual(self.runCli(['bad-command']), 2)

  def testPlatformWrapperForwardsInstallAndCheck(self) -> None:
    """功能：启动脚本不拆分含空格路径并传递子命令状态。

    入参：无。
    返回值：无。
    边界情况：仅运行当前平台实际可调用的包装脚本。
    """
    if os.environ.get('CODEX_MEMORY_SKIP_WRAPPER_TEST') == '1':
      self.skipTest('当前 CI runner 的外部 shell 参数转发由专用 smoke 验证')
    repositoryRoot = Path(__file__).resolve().parents[1]
    targetRoot = self.tempRoot / 'folder with spaces' / 'memory data'
    if os.name == 'nt':
      wrapperCommand = [
        'powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass',
        '-File', str(repositoryRoot / 'codex-memory.ps1'),
      ]
    else:
      shellPath = shutil.which('sh')
      if shellPath is None:
        self.skipTest('当前平台没有 POSIX shell')
      wrapperCommand = [shellPath, str(repositoryRoot / 'codex-memory')]
    installed = subprocess.run(
      [*wrapperCommand, 'install', '--root', str(targetRoot), '--non-interactive'],
      cwd=repositoryRoot, capture_output=True, text=True, encoding='utf-8',
      errors='replace', check=False,
    )
    self.assertEqual(installed.returncode, 0, installed.stderr)
    self.assertTrue((targetRoot / 'MEMORY.md').is_file())
    checked = subprocess.run(
      [*wrapperCommand, 'check', '--root', str(targetRoot)],
      cwd=repositoryRoot, capture_output=True, text=True, encoding='utf-8',
      errors='replace', check=False,
    )
    self.assertIn(checked.returncode, (0, 1), checked.stderr)
    self.assertIn('PASS', checked.stdout)
    instructions = subprocess.run(
      [*wrapperCommand, 'instructions', '--root', str(targetRoot)],
      cwd=repositoryRoot, capture_output=True, text=True, encoding='utf-8',
      errors='replace', check=False,
    )
    self.assertEqual(instructions.returncode, 0, instructions.stderr)
    self.assertIn('codex_memory', instructions.stdout)
    (targetRoot / 'MEMORY.md').unlink()
    failed = subprocess.run(
      [*wrapperCommand, 'check', '--root', str(targetRoot)],
      cwd=repositoryRoot, capture_output=True, text=True, encoding='utf-8',
      errors='replace', check=False,
    )
    self.assertEqual(failed.returncode, 1, failed.stderr)
    invalid = subprocess.run(
      [*wrapperCommand, 'unknown-command'],
      cwd=repositoryRoot, capture_output=True, text=True, encoding='utf-8',
      errors='replace', check=False,
    )
    self.assertEqual(invalid.returncode, 2, invalid.stderr)


if __name__ == '__main__':
  unittest.main()
