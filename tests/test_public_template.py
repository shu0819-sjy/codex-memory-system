"""公开模板隐私检查与回归测试。"""

import tempfile
import unittest
import re
from pathlib import Path


PUBLIC_FILES = ('README.md', 'AGENTS.example.md')
PUBLIC_DIRECTORIES = ('memory', 'docs')
PRIVATE_PATTERNS = (
  re.compile(r'(?i)\b[A-Z]:\\Users\\[^\\\s"`<>]+(?:\\|$|(?=[\s"`<>]))'),
  re.compile(r'/(?:Users|home)/[^/\s"`<>]+(?:/|$|(?=[\s"`<>]))'),
  re.compile(
    r'''(?ix)\b(?:api[_-]?key|access[_-]?token|password|secret)
    ["']?\s*[:=]\s*["']?(?!<|\$\{|\[)[a-z0-9][a-z0-9_.-]{7,}'''
  ),
  re.compile(r'\b(?:ghp_[A-Za-z0-9]{36}|sk-[A-Za-z0-9]{20,})\b'),
)
PEM_BEGIN_PATTERN = re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----')
PEM_BODY_PATTERN = re.compile(r'^[A-Za-z0-9+/=]{32,}$')


def scanPublicTemplate(repositoryRoot: Path) -> list[tuple[str, int]]:
  """功能：只扫描指定公开模板的完整主目录和凭据形态。

  入参：repositoryRoot 为待检查的仓库根目录。
  返回值：仅含相对路径和一基行号的命中列表；零表示无法安全读取。
  边界情况：缺失的可选文件跳过，符号链接或编码错误按失败处理，绝不返回原文。
  """
  candidates = [repositoryRoot / name for name in PUBLIC_FILES]
  for directoryName in PUBLIC_DIRECTORIES:
    directoryPath = repositoryRoot / directoryName
    if directoryPath.is_symlink():
      candidates.append(directoryPath)
    elif directoryPath.is_dir():
      candidates.extend(
        path for path in directoryPath.rglob('*') if path.is_symlink() or not path.is_dir()
      )
  findings: list[tuple[str, int]] = []
  for candidate in sorted(candidates):
    if not candidate.exists() and not candidate.is_symlink():
      continue
    relativePath = candidate.relative_to(repositoryRoot).as_posix()
    if candidate.is_symlink() or not candidate.is_file():
      findings.append((relativePath, 0))
      continue
    try:
      lines = candidate.read_text(encoding='utf-8').splitlines()
    except (OSError, UnicodeError):
      findings.append((relativePath, 0))
      continue
    for lineNumber, line in enumerate(lines, start=1):
      hasPemBody = (
        lineNumber < len(lines) and PEM_BEGIN_PATTERN.search(line)
        and PEM_BODY_PATTERN.fullmatch(lines[lineNumber].strip())
      )
      if hasPemBody or any(pattern.search(line) for pattern in PRIVATE_PATTERNS):
        findings.append((relativePath, lineNumber))
  return findings


class PublicTemplateTests(unittest.TestCase):
  def testAcceptsSafeTemplateAndDocumentationExamples(self) -> None:
    """功能：验证无真实值的配置和说明文字可公开；入参：无；返回值：无；边界：风险前缀本身不是秘密。"""
    with tempfile.TemporaryDirectory() as temporaryPath:
      repositoryRoot = Path(temporaryPath)
      (repositoryRoot / 'memory').mkdir()
      (repositoryRoot / 'docs').mkdir()
      (repositoryRoot / 'memory' / 'config.json').write_text(
        '{"privacyPatterns": ["api_key=", "C:\\\\Users\\\\"]}', encoding='utf-8'
      )
      (repositoryRoot / 'README.md').write_text(
        '示例：C:\\Users\\ 和 /Users/ 是需要注意的路径前缀。\n', encoding='utf-8'
      )
      (repositoryRoot / 'docs' / 'guide.md').write_text(
        '使用 <TOKEN> 作为占位符。\n', encoding='utf-8'
      )
      self.assertEqual([], scanPublicTemplate(repositoryRoot))

  def testRejectsConcretePrivatePathsWithoutEchoingThem(self) -> None:
    """功能：验证完整主目录路径报告位置且不泄漏路径；入参：无；返回值：无；边界：含三个系统的伪造路径。"""
    with tempfile.TemporaryDirectory() as temporaryPath:
      repositoryRoot = Path(temporaryPath)
      (repositoryRoot / 'docs').mkdir()
      (repositoryRoot / 'docs' / 'notes.md').write_text(
        'C:\\Users\\sampleuser\\notes\n/Users/sampleuser/notes\n/home/sampleuser/notes\n'
        'C:\\Users\\sampleuser\n/home/sampleuser\n',
        encoding='utf-8',
      )
      findings = scanPublicTemplate(repositoryRoot)
      self.assertEqual(
        [('docs/notes.md', 1), ('docs/notes.md', 2), ('docs/notes.md', 3),
         ('docs/notes.md', 4), ('docs/notes.md', 5)], findings
      )
      self.assertNotIn('sampleuser', repr(findings))

  def testRejectsCredentialAssignmentsAndTokenShapes(self) -> None:
    """功能：验证凭据赋值及令牌形状被识别；入参：无；返回值：无；边界：多个命中同一行只报告一次。"""
    with tempfile.TemporaryDirectory() as temporaryPath:
      repositoryRoot = Path(temporaryPath)
      (repositoryRoot / 'memory').mkdir()
      (repositoryRoot / 'memory' / 'MEMORY.md').write_text(
        'api_key=fictional-value-12345 secret: fictional-value-23456\n'
        'ghp_' + 'A' * 36 + '\n', encoding='utf-8'
      )
      self.assertEqual(
        [('memory/MEMORY.md', 1), ('memory/MEMORY.md', 2)],
        scanPublicTemplate(repositoryRoot),
      )

  def testScansOnlyPublicContent(self) -> None:
    """功能：验证私有测试夹具不被公开内容检查捕获；入参：无；返回值：无；边界：不存在的公开目录可跳过。"""
    with tempfile.TemporaryDirectory() as temporaryPath:
      repositoryRoot = Path(temporaryPath)
      (repositoryRoot / 'tests').mkdir()
      (repositoryRoot / 'tests' / 'fixture.txt').write_text(
        'password=fictional-value-12345\n', encoding='utf-8'
      )
      self.assertEqual([], scanPublicTemplate(repositoryRoot))

  def testRejectsPrivateKeyBlockButAllowsMarkerExample(self) -> None:
    """功能：验证真实 PEM 区块需要密钥体才命中；入参：无；返回值：无；边界：单独的示例标签安全。"""
    with tempfile.TemporaryDirectory() as temporaryPath:
      repositoryRoot = Path(temporaryPath)
      (repositoryRoot / 'docs').mkdir()
      (repositoryRoot / 'docs' / 'guide.md').write_text(
        '不要公开 -----BEGIN PRIVATE KEY-----\n'
        '-----BEGIN PRIVATE KEY-----\n' + 'A' * 48 + '\n', encoding='utf-8'
      )
      self.assertEqual([('docs/guide.md', 2)], scanPublicTemplate(repositoryRoot))

  def testRepositoryPublicContentIsSafe(self) -> None:
    """功能：验证当前公开模板无隐私命中；入参：无；返回值：无；边界：输出仅包含相对路径和行号。"""
    repositoryRoot = Path(__file__).resolve().parents[1]
    self.assertEqual([], scanPublicTemplate(repositoryRoot))
