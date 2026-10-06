"""记忆目录的跨平台只读自检器。"""

import codecs
import os
import re
import stat
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from codex_memory.config import MemoryConfig


VALID_SEVERITIES = frozenset(('PASS', 'WARN', 'FAIL'))
ENTRY_PATTERN = re.compile(r'^- (\d{4}-\d{2}-\d{2})｜')
MALFORMED_ENTRY_PATTERN = re.compile(r'^- \d{4}-\d{2}-\d{2}(?!｜)')
UPDATED_DATE_PATTERN = re.compile(r'更新：(\d{4}-\d{2}-\d{2})')
INDEX_PATH_PATTERN = re.compile(r'spaces[\\/]([^`\\/]+)\.md')


@dataclass(frozen=True)
class CheckResult:
  """表示一条不包含记忆正文的不可变检查结果。"""

  severity: str
  message: str
  path: str | None = None
  line: int | None = None

  def __post_init__(self) -> None:
    """功能：限制诊断级别为公开契约允许的三个值。

    入参：无，使用当前实例的 severity 字段。
    返回值：无。
    边界情况：任何非 PASS、WARN、FAIL 的值都抛出 ValueError。
    """
    if self.severity not in VALID_SEVERITIES:
      raise ValueError('无效的检查结果级别')


class UnsafePathError(OSError):
  """表示待读路径为链接、重解析点或解析到记忆根目录之外。"""


def _relativePath(memoryRoot: Path, path: Path) -> str:
  """功能：将记忆目录下的路径转换为跨平台诊断路径。

  入参：memoryRoot 为记忆根目录，path 为其下待转换路径。
  返回值：使用正斜杠的相对路径。
  边界情况：path 应位于 memoryRoot 下，输入来自检查器自己构造的安全路径。
  """
  return path.relative_to(memoryRoot).as_posix()


def _isLinkLike(path: Path) -> bool:
  """功能：使用不跟随的 lstat 识别符号链接和 Windows 重解析点。

  入参：path 为待检查的单一路径组件。
  返回值：符号链接、junction 或其他重解析点返回 True，普通路径返回 False。
  边界情况：路径不存在时返回 False，由后续结构检查负责报告缺失。
  """
  try:
    pathStat = path.lstat()
  except (FileNotFoundError, OSError):
    return False
  if stat.S_ISLNK(pathStat.st_mode):
    return True
  reparseAttribute = getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0)
  fileAttributes = getattr(pathStat, 'st_file_attributes', 0)
  return bool(reparseAttribute and fileAttributes & reparseAttribute)


def _pathSecurityIssue(memoryRoot: Path, path: Path) -> str | None:
  """功能：检查路径及其根目录内父级是否可安全读取或枚举。

  入参：memoryRoot 为记忆根目录，path 为待校验路径。
  返回值：安全时返回 None，链接/重解析或越界时返回固定脱敏原因。
  边界情况：不存在路径留给结构检查报告；解析错误按不安全失败关闭。
  """
  try:
    relativeParts = path.relative_to(memoryRoot).parts
  except ValueError:
    return '路径越出记忆根目录'
  currentPath = memoryRoot
  if _isLinkLike(currentPath):
    return '路径是符号链接或重解析点'
  for part in relativeParts:
    currentPath = currentPath / part
    if _isLinkLike(currentPath):
      return '路径是符号链接或重解析点'
  if not path.exists():
    return None
  try:
    rootResolved = memoryRoot.resolve(strict=True)
    path.resolve(strict=True).relative_to(rootResolved)
  except (OSError, RuntimeError, ValueError):
    return '路径解析到记忆根目录之外'
  return None


def _readBoundedText(
  memoryRoot: Path,
  path: Path,
  limitBytes: int,
) -> tuple[str | None, bool, bool]:
  """功能：以 UTF-8 有界读取文件，并报告超限与 BOM 状态。

  入参：memoryRoot 为记忆根目录，path 为待读文件，limitBytes 为配置字节上限。
  返回值：文本或 None、是否超限、是否含 UTF-8 BOM 的三元组。
  边界情况：支持时用 O_NOFOLLOW 打开，打开后再校验路径；无效 UTF-8 返回 None。
  """
  readLimit = limitBytes + 4
  securityIssue = _pathSecurityIssue(memoryRoot, path)
  if securityIssue is not None:
    raise UnsafePathError(securityIssue)
  openFlags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
  fileDescriptor = os.open(path, openFlags)
  try:
    securityIssue = _pathSecurityIssue(memoryRoot, path)
    if securityIssue is not None:
      raise UnsafePathError(securityIssue)
    fileStat = os.fstat(fileDescriptor)
    if not stat.S_ISREG(fileStat.st_mode):
      raise UnsafePathError('路径不是普通文件')
    content = os.read(fileDescriptor, readLimit)
    isOversized = fileStat.st_size > limitBytes
  finally:
    os.close(fileDescriptor)
  hasBom = content.startswith(b'\xef\xbb\xbf')
  contentWithoutBom = content[3:] if hasBom else content
  try:
    decoder = codecs.getincrementaldecoder('utf-8')()
    text = decoder.decode(contentWithoutBom, final=not isOversized)
  except UnicodeDecodeError:
    return None, isOversized, hasBom
  return text, isOversized, hasBom


def _inspectTextFile(
  memoryRoot: Path,
  path: Path,
  limitBytes: int,
  results: list[CheckResult],
) -> str | None:
  """功能：检查文件大小、BOM 和 UTF-8 编码，并返回有界文本。

  入参：memoryRoot 为根目录，path 为文件，limitBytes 为上限，results 为诊断容器。
  返回值：可解码时返回有界文本，否则返回 None。
  边界情况：超限文件仍检查前缀内容，但不读取全文。
  """
  relativePath = _relativePath(memoryRoot, path)
  securityIssue = _pathSecurityIssue(memoryRoot, path)
  if securityIssue is not None:
    results.append(CheckResult('FAIL', securityIssue, relativePath))
    return None
  try:
    text, isOversized, hasBom = _readBoundedText(memoryRoot, path, limitBytes)
  except UnsafePathError:
    results.append(CheckResult('FAIL', '路径是符号链接、重解析点或越界路径', relativePath))
    return None
  except OSError:
    results.append(CheckResult('FAIL', '无法读取文件', relativePath))
    return None
  if isOversized:
    results.append(CheckResult('FAIL', '文件大小超过配置上限', relativePath))
  if hasBom:
    results.append(CheckResult('FAIL', '文件含 UTF-8 BOM', relativePath))
  if text is None:
    results.append(CheckResult('FAIL', '文件不是有效 UTF-8', relativePath))
  return text


def _checkPrivacy(
  text: str,
  relativePath: str,
  privacyPatterns: tuple[str, ...],
  results: list[CheckResult],
) -> None:
  """功能：按大小写无关字面量检查文本中的隐私风险。

  入参：text 为有界文本，relativePath 为路径，privacyPatterns 为字面量，results 为诊断容器。
  返回值：无，匹配时追加脱敏诊断。
  边界情况：同一行命中多个模式仅输出一条，消息不包含模式或源文本。
  """
  normalizedPatterns = tuple(pattern.casefold() for pattern in privacyPatterns)
  for lineNumber, lineText in enumerate(text.splitlines(), start=1):
    normalizedLine = lineText.casefold()
    if any(pattern in normalizedLine for pattern in normalizedPatterns):
      results.append(CheckResult('FAIL', '检测到隐私风险', relativePath, lineNumber))


def _checkEntries(
  text: str,
  relativePath: str,
  entryLocations: dict[str, list[tuple[str, int]]],
  results: list[CheckResult],
) -> None:
  """功能：检查记忆条目分隔符并收集脱敏重复键。

  入参：text 为文本，relativePath 为路径，entryLocations 为重复索引，results 为诊断容器。
  返回值：无，追加格式诊断并更新重复索引。
  边界情况：重复键只在内存中使用，从不写入诊断消息。
  """
  for lineNumber, lineText in enumerate(text.splitlines(), start=1):
    if MALFORMED_ENTRY_PATTERN.match(lineText):
      results.append(CheckResult('WARN', '记忆条目缺少｜分隔符', relativePath, lineNumber))
      continue
    if ENTRY_PATTERN.match(lineText):
      normalizedEntry = ''.join(lineText.split())
      entryLocations.setdefault(normalizedEntry, []).append((relativePath, lineNumber))


def _checkActiveEntries(
  text: str,
  config: MemoryConfig,
  results: list[CheckResult],
) -> None:
  """功能：检查热记忆进行中条目的数量和更新日期。

  入参：text 为 MEMORY.md 文本，config 为配置，results 为诊断容器。
  返回值：无，追加超量或过期诊断。
  边界情况：无进行中章节时输出 WARN；日期无效或缺失时也只报定位信息。
  """
  lines = text.splitlines()
  sectionStart = next(
    (index for index, lineText in enumerate(lines) if lineText.startswith('## 进行中')),
    None,
  )
  if sectionStart is None:
    results.append(CheckResult('WARN', '缺少进行中章节', 'MEMORY.md'))
    return
  activeEntries: list[tuple[int, str]] = []
  for index in range(sectionStart + 1, len(lines)):
    lineText = lines[index]
    if lineText.startswith('## '):
      break
    if lineText.startswith('- '):
      activeEntries.append((index + 1, lineText))
  if len(activeEntries) > config.activeTaskLimit:
    results.append(CheckResult('FAIL', '进行中条目超过配置上限', 'MEMORY.md'))
  today = date.today()
  for lineNumber, lineText in activeEntries:
    dateMatch = UPDATED_DATE_PATTERN.search(lineText)
    if dateMatch is None:
      results.append(CheckResult('WARN', '进行中条目缺少更新日期', 'MEMORY.md', lineNumber))
      continue
    try:
      updatedDate = datetime.strptime(dateMatch.group(1), '%Y-%m-%d').date()
    except ValueError:
      results.append(CheckResult('WARN', '进行中条目更新日期无效', 'MEMORY.md', lineNumber))
      continue
    if (today - updatedDate).days > config.staleDays:
      results.append(CheckResult('WARN', '进行中条目已过期', 'MEMORY.md', lineNumber))


def _checkIndex(
  text: str,
  config: MemoryConfig,
  results: list[CheckResult],
) -> None:
  """功能：校验 MEMORY.md 分类索引与配置分类一致。

  入参：text 为 MEMORY.md 文本，config 为配置，results 为诊断容器。
  返回值：无，追加缺失或多余索引诊断。
  边界情况：多余索引报告其行号，缺失索引无可定位行号。
  """
  indexedCategories: dict[str, list[int]] = {}
  for lineNumber, lineText in enumerate(text.splitlines(), start=1):
    for match in INDEX_PATH_PATTERN.finditer(lineText):
      indexedCategories.setdefault(match.group(1), []).append(lineNumber)
  configuredCategories = set(config.categories)
  for category in config.categories:
    if category not in indexedCategories:
      results.append(CheckResult('FAIL', '分类索引缺少配置分类', 'MEMORY.md'))
  for category, lineNumbers in indexedCategories.items():
    if category not in configuredCategories:
      for lineNumber in lineNumbers:
        results.append(CheckResult('FAIL', '分类索引包含未配置分类', 'MEMORY.md', lineNumber))


def _checkDuplicates(
  entryLocations: dict[str, list[tuple[str, int]]],
  results: list[CheckResult],
) -> None:
  """功能：将重复条目索引转换为不回显正文的诊断。

  入参：entryLocations 为内存重复索引，results 为诊断容器。
  返回值：无，为每个重复位置追加诊断。
  边界情况：跨文件重复为 FAIL，单文件内重复为 WARN。
  """
  for locations in entryLocations.values():
    if len(locations) < 2:
      continue
    severity = 'FAIL' if len({path for path, _ in locations}) > 1 else 'WARN'
    for relativePath, lineNumber in locations:
      results.append(CheckResult(severity, '检测到重复记忆条目', relativePath, lineNumber))


def checkMemory(memoryRoot: Path, config: MemoryConfig) -> list[CheckResult]:
  """功能：按配置对记忆目录执行结构、内容和隐私只读检查。

  入参：memoryRoot 为待检查记忆根目录，config 为已验证的 MemoryConfig。
  返回值：CheckResult 列表，无失败时至少包含一条 PASS。
  边界情况：缺失路径只诊断不创建；诊断不包含记忆正文或隐私匹配文本。
  """
  results: list[CheckResult] = []
  entryLocations: dict[str, list[tuple[str, int]]] = {}
  requiredPaths = (
    (memoryRoot / 'MEMORY.md', False),
    (memoryRoot / 'RULES.md', False),
    (memoryRoot / 'spaces', True),
    (memoryRoot / 'ARCHIVE', True),
  )
  unsafePaths: set[Path] = set()
  for requiredPath, shouldBeDirectory in requiredPaths:
    securityIssue = _pathSecurityIssue(memoryRoot, requiredPath)
    if securityIssue is not None:
      results.append(CheckResult('FAIL', securityIssue, _relativePath(memoryRoot, requiredPath)))
      unsafePaths.add(requiredPath)
      continue
    pathExists = requiredPath.is_dir() if shouldBeDirectory else requiredPath.is_file()
    if not pathExists:
      results.append(CheckResult('FAIL', '缺失必需路径', _relativePath(memoryRoot, requiredPath)))

  memoryPath = memoryRoot / 'MEMORY.md'
  if memoryPath not in unsafePaths and memoryPath.is_file():
    memoryText = _inspectTextFile(
      memoryRoot,
      memoryPath,
      config.hotLimitBytes,
      results,
    )
    if memoryText is not None:
      _checkIndex(memoryText, config, results)
      _checkActiveEntries(memoryText, config, results)
      _checkEntries(memoryText, 'MEMORY.md', entryLocations, results)
      _checkPrivacy(memoryText, 'MEMORY.md', config.privacyPatterns, results)

  rulesPath = memoryRoot / 'RULES.md'
  if rulesPath not in unsafePaths and rulesPath.is_file():
    rulesText = _inspectTextFile(
      memoryRoot,
      rulesPath,
      config.rulesLimitBytes,
      results,
    )
    if rulesText is not None:
      _checkEntries(rulesText, 'RULES.md', entryLocations, results)
      _checkPrivacy(rulesText, 'RULES.md', config.privacyPatterns, results)

  spacesRoot = memoryRoot / 'spaces'
  if spacesRoot not in unsafePaths and spacesRoot.is_dir():
    configuredPaths = {f'{category}.md' for category in config.categories}
    reportedUnsafePaths: set[Path] = set()
    for category in config.categories:
      categoryPath = spacesRoot / f'{category}.md'
      relativePath = _relativePath(memoryRoot, categoryPath)
      securityIssue = _pathSecurityIssue(memoryRoot, categoryPath)
      if securityIssue is not None:
        results.append(CheckResult('FAIL', securityIssue, relativePath))
        reportedUnsafePaths.add(categoryPath)
        continue
      if not categoryPath.is_file():
        results.append(CheckResult('FAIL', '缺失配置分类文件', relativePath))
        continue
      categoryText = _inspectTextFile(
        memoryRoot,
        categoryPath,
        config.spaceLimitBytes,
        results,
      )
      if categoryText is not None:
        _checkEntries(categoryText, relativePath, entryLocations, results)
        _checkPrivacy(categoryText, relativePath, config.privacyPatterns, results)
    try:
      inventory = tuple(spacesRoot.iterdir())
    except OSError:
      results.append(CheckResult('FAIL', '无法读取分类目录', 'spaces'))
      inventory = ()
    for categoryPath in inventory:
      securityIssue = _pathSecurityIssue(memoryRoot, categoryPath)
      if securityIssue is not None:
        if categoryPath not in reportedUnsafePaths:
          results.append(CheckResult('FAIL', securityIssue, _relativePath(memoryRoot, categoryPath)))
        continue
      if categoryPath.is_file() and categoryPath.suffix == '.md' and categoryPath.name not in configuredPaths:
        results.append(CheckResult('FAIL', '分类目录包含未配置文件', _relativePath(memoryRoot, categoryPath)))

  _checkDuplicates(entryLocations, results)

  archiveRoot = memoryRoot / 'ARCHIVE'
  if archiveRoot not in unsafePaths and archiveRoot.is_dir():
    archivePattern = re.compile(config.archiveNamePattern)
    try:
      archivePaths = tuple(archiveRoot.iterdir())
    except OSError:
      results.append(CheckResult('FAIL', '无法读取归档目录', 'ARCHIVE'))
      archivePaths = ()
    for archivePath in archivePaths:
      securityIssue = _pathSecurityIssue(memoryRoot, archivePath)
      if securityIssue is not None:
        results.append(CheckResult('FAIL', securityIssue, _relativePath(memoryRoot, archivePath)))
        continue
      if archivePath.is_file() and archivePath.name != '.gitkeep' and archivePattern.fullmatch(archivePath.name) is None:
        results.append(CheckResult('WARN', '归档文件命名不匹配配置规则', _relativePath(memoryRoot, archivePath)))

  if not any(result.severity == 'FAIL' for result in results):
    results.append(CheckResult('PASS', '记忆结构检查通过'))
  return results
