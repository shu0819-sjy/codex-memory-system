"""跨平台记忆模板安装、安全更新与失败回滚。"""

import errno
import os
import shutil
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path

from codex_memory.config import ConfigError, MemoryConfig, loadConfig


EXCLUDED_TEMPLATE_PATHS = frozenset(('check-memory.ps1',))
REPARSE_POINT_ATTRIBUTE = getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)
REQUIRED_TEMPLATE_DIRECTORIES = ('spaces', 'ARCHIVE')
REQUIRED_TEMPLATE_FILES = ('MEMORY.md', 'RULES.md', 'config.json')


class InstallError(RuntimeError):
  """表示安装或更新未能安全完成。"""


@dataclass(frozen=True)
class InstallReport:
  """表示一次成功安装或更新产生的路径与版本信息。"""

  targetRoot: Path
  createdPaths: tuple[Path, ...]
  backupPath: Path | None
  templateVersion: str


@dataclass(frozen=True)
class OwnedPath:
  """表示当前调用创建且可通过 lstat 身份安全识别的路径。"""

  path: Path
  device: int
  inode: int
  mode: int
  size: int
  modifiedNanoseconds: int


def _pathExists(path: Path) -> bool:
  """功能：判断路径本身是否存在，包括指向不存在目标的链接。

  入参：path 为待判断路径。
  返回值：目录项存在时返回 True，否则返回 False。
  边界情况：使用 lexists 避免把断开的符号链接误判为安全的缺失路径。
  """
  return os.path.lexists(path)


def _captureOwnedPath(path: Path) -> OwnedPath:
  """功能：记录当前调用刚创建路径的 lstat 身份和可变状态。

  入参：path 为已成功创建且不应为链接的路径。
  返回值：用于回滚时确认所有权的 OwnedPath。
  边界情况：路径在捕获前消失或变成链接时抛出 OSError，不宣称拥有该路径。
  """
  pathStat = os.lstat(path)
  if stat.S_ISLNK(pathStat.st_mode) or bool(
    getattr(pathStat, 'st_file_attributes', 0) & REPARSE_POINT_ATTRIBUTE
  ):
    raise OSError(f'新建路径意外变成链接或重解析点: {path}')
  return _ownedPathFromStat(path, pathStat)


def _ownedPathFromStat(path: Path, pathStat: os.stat_result) -> OwnedPath:
  """功能：用已取得的 stat 结果构造不会再次访问路径的所有权记录。

  入参：path 为记录使用的路径，pathStat 为该对象通过 lstat 或 fstat 取得的状态。
  返回值：绑定指定路径但继承已知对象身份的 OwnedPath。
  边界情况：调用方负责保证 pathStat 对应本次创建对象；本函数不读取可能已被替换的路径。
  """
  return OwnedPath(
    path=path,
    device=pathStat.st_dev,
    inode=pathStat.st_ino,
    mode=stat.S_IFMT(pathStat.st_mode),
    size=pathStat.st_size,
    modifiedNanoseconds=pathStat.st_mtime_ns,
  )


def _moveOwnedPath(ownedPath: OwnedPath, destinationPath: Path) -> OwnedPath:
  """功能：在不重新读取文件系统的情况下把所有权身份映射到落位后的新路径。

  入参：ownedPath 为临时对象身份，destinationPath 为原子落位后的路径。
  返回值：身份与状态不变、路径改为 destinationPath 的 OwnedPath。
  边界情况：仅适用于 rename、replace 或硬链接后临时链接删除等保持对象身份的操作。
  """
  return OwnedPath(
    path=destinationPath,
    device=ownedPath.device,
    inode=ownedPath.inode,
    mode=ownedPath.mode,
    size=ownedPath.size,
    modifiedNanoseconds=ownedPath.modifiedNanoseconds,
  )


def _stillOwnsPath(ownedPath: OwnedPath) -> bool:
  """功能：确认路径仍是当前调用创建且未被替换或原位修改的对象。

  入参：ownedPath 为创建成功后捕获的身份记录。
  返回值：身份、类型、大小和修改时间均一致时返回 True，否则返回 False。
  边界情况：路径消失、被换成链接或读取失败时返回 False。
  """
  pathStat = _matchingIdentityStat(ownedPath)
  if pathStat is None:
    return False
  if ownedPath.mode == stat.S_IFDIR:
    return True
  return (
    pathStat.st_size,
    pathStat.st_mtime_ns,
  ) == (
    ownedPath.size,
    ownedPath.modifiedNanoseconds,
  )


def _matchingIdentityStat(ownedPath: OwnedPath) -> os.stat_result | None:
  """功能：读取路径状态并确认其设备、inode 和类型仍与所有权记录一致。

  入参：ownedPath 为此前记录的对象身份。
  返回值：身份匹配时返回当前 lstat，路径消失、变成链接或身份变化时返回 None。
  边界情况：只比较对象身份，不判断文件内容状态，供失败清理独占创建的半成品使用。
  """
  try:
    pathStat = os.lstat(ownedPath.path)
  except OSError:
    return None
  isLinkLike = stat.S_ISLNK(pathStat.st_mode) or bool(
    getattr(pathStat, 'st_file_attributes', 0) & REPARSE_POINT_ATTRIBUTE
  )
  hasSameIdentity = not isLinkLike and (
    pathStat.st_dev,
    pathStat.st_ino,
    stat.S_IFMT(pathStat.st_mode),
  ) == (
    ownedPath.device,
    ownedPath.inode,
    ownedPath.mode,
  )
  return pathStat if hasSameIdentity else None


def _isLinkLike(path: Path) -> bool:
  """功能：识别符号链接以及 Windows junction 或其他重解析点。

  入参：path 为已存在或可能存在的路径。
  返回值：路径是链接类目录项时返回 True，否则返回 False。
  边界情况：路径不存在时返回 False；Python 3.11 也通过文件属性识别重解析点。
  """
  try:
    pathStat = os.lstat(path)
  except FileNotFoundError:
    return False
  return stat.S_ISLNK(pathStat.st_mode) or bool(
    getattr(pathStat, 'st_file_attributes', 0) & REPARSE_POINT_ATTRIBUTE
  )


def _assertSafeAncestors(path: Path, label: str) -> None:
  """功能：确认路径及其所有现存祖先都不是链接或重解析点。

  入参：path 为目标路径，label 为安全错误中的路径角色。
  返回值：无，发现链接类祖先时抛出 InstallError。
  边界情况：不存在的中间目录会跳过，检查持续到文件系统根目录。
  """
  currentPath = path.absolute()
  while True:
    if _pathExists(currentPath) and _isLinkLike(currentPath):
      raise InstallError(f'{label}包含链接或重解析点: {currentPath}')
    if currentPath.parent == currentPath:
      return
    currentPath = currentPath.parent


def _assertDisjointTrees(sourceRoot: Path, targetRoot: Path) -> None:
  """功能：拒绝源模板与目标目录相同或互相包含的路径组合。

  入参：sourceRoot 为仓库 memory 源树，targetRoot 为个人记忆目标树。
  返回值：无，路径重叠时抛出 InstallError。
  边界情况：目标或其父目录尚不存在时仍按绝对规范化路径检查前缀关系。
  """
  sourceAbsolute = sourceRoot.absolute()
  targetAbsolute = targetRoot.absolute()
  try:
    targetAbsolute.relative_to(sourceAbsolute)
    raise InstallError(f'源模板与目标目录重叠: {targetRoot}')
  except ValueError:
    pass
  try:
    sourceAbsolute.relative_to(targetAbsolute)
    raise InstallError(f'源模板与目标目录重叠: {targetRoot}')
  except ValueError:
    pass


def _walkTree(root: Path, label: str) -> tuple[Path, ...]:
  """功能：不跟随链接地枚举目录树，并拒绝任何链接或重解析点。

  入参：root 为树根目录，label 为安全错误中的路径角色。
  返回值：按相对路径稳定排序的后代路径元组，不包含 root。
  边界情况：根不存在、不是目录或任一后代是链接时抛出 InstallError。
  """
  _assertSafeAncestors(root, label)
  if not root.is_dir():
    raise InstallError(f'{label}不是有效目录: {root}')
  discoveredPaths: list[Path] = []
  pendingDirectories = [root]
  while pendingDirectories:
    currentRoot = pendingDirectories.pop()
    try:
      childPaths = sorted(currentRoot.iterdir(), key=lambda item: item.name)
    except OSError as error:
      raise InstallError(f'无法读取{label}: {currentRoot}') from error
    for childPath in childPaths:
      if _isLinkLike(childPath):
        raise InstallError(f'{label}包含链接或重解析点: {childPath}')
      discoveredPaths.append(childPath)
      if childPath.is_dir():
        pendingDirectories.append(childPath)
      elif not childPath.is_file():
        raise InstallError(f'{label}包含不支持的路径类型: {childPath}')
  return tuple(sorted(discoveredPaths, key=lambda item: item.relative_to(root).as_posix()))


def _readTemplateVersion(repositoryRoot: Path) -> str:
  """功能：从仓库根目录读取单行、非空的模板版本。

  入参：repositoryRoot 为仓库根目录。
  返回值：去除行尾空白后的版本字符串。
  边界情况：VERSION 缺失、是链接、不是普通文件、为空或包含多行时抛出 InstallError。
  """
  versionPath = repositoryRoot / 'VERSION'
  _assertSafeAncestors(versionPath, '版本文件路径')
  if not versionPath.is_file():
    raise InstallError(f'缺少 VERSION 文件: {versionPath}')
  try:
    versionText = versionPath.read_text(encoding='utf-8')
  except (OSError, UnicodeError) as error:
    raise InstallError(f'无法读取 VERSION 文件: {versionPath}') from error
  versionLines = versionText.splitlines()
  if len(versionLines) != 1 or not versionLines[0].strip():
    raise InstallError('VERSION 必须只包含一个非空版本号')
  return versionLines[0].strip()


def _validateSourceTemplate(sourceRoot: Path) -> tuple[MemoryConfig, tuple[Path, ...]]:
  """功能：预检公开模板结构、配置和全部目录项的安全性。

  入参：sourceRoot 为 repositoryRoot 下的 memory 目录。
  返回值：经校验的配置和不含链接的源路径元组。
  边界情况：缺少必需路径、分类模板、配置无效或路径类型错误时抛出 InstallError。
  """
  sourcePaths = _walkTree(sourceRoot, '源模板')
  for relativePath in REQUIRED_TEMPLATE_DIRECTORIES:
    requiredPath = sourceRoot / relativePath
    if not requiredPath.is_dir():
      raise InstallError(f'源模板缺少目录: {relativePath}')
  for relativePath in REQUIRED_TEMPLATE_FILES:
    requiredPath = sourceRoot / relativePath
    if not requiredPath.is_file():
      raise InstallError(f'源模板缺少文件: {relativePath}')
  try:
    config = loadConfig(sourceRoot)
  except ConfigError as error:
    raise InstallError(f'源模板配置无效: {error}') from error
  for category in config.categories:
    categoryPath = sourceRoot / 'spaces' / f'{category}.md'
    if not categoryPath.is_file():
      raise InstallError(f'源模板缺少分类文件: spaces/{category}.md')
  return config, sourcePaths


def _selectTemplatePaths(sourceRoot: Path, sourcePaths: tuple[Path, ...]) -> tuple[Path, ...]:
  """功能：从安全预检结果中选出应复制到个人记忆目录的数据模板。

  入参：sourceRoot 为模板根，sourcePaths 为已预检路径。
  返回值：排除仓库工具脚本后的稳定路径元组。
  边界情况：排除项若将来成为目录，其全部后代也会被排除。
  """
  selectedPaths: list[Path] = []
  for sourcePath in sourcePaths:
    relativeText = sourcePath.relative_to(sourceRoot).as_posix()
    isExcluded = any(
      relativeText == excludedPath or relativeText.startswith(f'{excludedPath}/')
      for excludedPath in EXCLUDED_TEMPLATE_PATHS
    )
    if not isExcluded:
      selectedPaths.append(sourcePath)
  return tuple(selectedPaths)


def _createMissingParents(parentRoot: Path) -> list[OwnedPath]:
  """功能：逐级创建目标所需的缺失父目录并记录本次创建项。

  入参：parentRoot 为必须存在的最深父目录。
  返回值：按创建顺序排列的新目录所有权记录列表。
  边界情况：遇到现存非目录或并发创建冲突时抛出 OSError，由调用方回滚已建空目录。
  """
  missingParents: list[Path] = []
  currentPath = parentRoot
  while not _pathExists(currentPath):
    missingParents.append(currentPath)
    currentPath = currentPath.parent
  if not currentPath.is_dir():
    raise OSError(f'父路径不是目录: {currentPath}')
  createdParents: list[OwnedPath] = []
  for missingPath in reversed(missingParents):
    missingPath.mkdir()
    createdParents.append(_captureOwnedPath(missingPath))
  return createdParents


def _removeCreatedPaths(createdPaths: list[OwnedPath]) -> tuple[Path, ...]:
  """功能：按逆序撤销当前更新新增的文件和空目录。

  入参：createdPaths 为当前调用成功创建路径的所有权记录。
  返回值：身份已变化或无法删除而保留的路径元组。
  边界情况：只用 unlink 或 rmdir，不递归删除；任何身份或内容变化均视为用户接管并保留。
  """
  retainedPaths: list[Path] = []
  for ownedPath in reversed(createdPaths):
    if not _stillOwnsPath(ownedPath):
      if _pathExists(ownedPath.path):
        retainedPaths.append(ownedPath.path)
      continue
    try:
      if ownedPath.mode == stat.S_IFREG:
        ownedPath.path.unlink()
      elif ownedPath.mode == stat.S_IFDIR:
        ownedPath.path.rmdir()
    except OSError:
      if _pathExists(ownedPath.path):
        retainedPaths.append(ownedPath.path)
  return tuple(retainedPaths)


def _removeEmptyParents(createdParents: list[OwnedPath]) -> None:
  """功能：逆序删除首次安装为目标创建且仍为空的父目录。

  入参：createdParents 为本次调用创建的父目录所有权记录列表。
  返回值：无。
  边界情况：目录已非空、消失或类型变化时停止删除并保留现场。
  """
  for ownedParent in reversed(createdParents):
    if not _stillOwnsPath(ownedParent):
      return
    try:
      ownedParent.path.rmdir()
    except OSError:
      return


def _copyTemplateTree(sourceRoot: Path, destinationRoot: Path, sourcePaths: tuple[Path, ...]) -> list[OwnedPath]:
  """功能：把预检后的模板路径以不可覆盖方式复制到独占目标目录。

  入参：sourceRoot 为模板根，destinationRoot 为已创建的空目标，sourcePaths 为待复制路径。
  返回值：按创建顺序排列的目标后代所有权记录列表。
  边界情况：任一目录或文件被并发创建时失败，调用方仅按身份回滚仍拥有的对象。
  """
  createdPaths: list[OwnedPath] = []
  directoryPaths = [path for path in sourcePaths if path.is_dir()]
  filePaths = [path for path in sourcePaths if path.is_file()]
  for sourcePath in directoryPaths:
    destinationPath = destinationRoot / sourcePath.relative_to(sourceRoot)
    destinationPath.mkdir()
    createdPaths.append(_captureOwnedPath(destinationPath))
  for sourcePath in filePaths:
    destinationPath = destinationRoot / sourcePath.relative_to(sourceRoot)
    createdPaths.append(_copyFileAtomically(sourcePath, destinationPath))
  return createdPaths


def _preflightUpdateTarget(targetRoot: Path, sourceRoot: Path, sourcePaths: tuple[Path, ...]) -> None:
  """功能：在更新写入前检查目标全树与模板路径类型兼容性。

  入参：targetRoot 为现有安装，sourceRoot 为模板根，sourcePaths 为待同步路径。
  返回值：无，发现链接、非目录根或文件/目录类型冲突时抛出 InstallError。
  边界情况：目标中与模板无关的普通自定义路径允许存在并保留。
  """
  _walkTree(targetRoot, '目标目录')
  for sourcePath in sourcePaths:
    destinationPath = targetRoot / sourcePath.relative_to(sourceRoot)
    if not _pathExists(destinationPath):
      continue
    if sourcePath.is_dir() and not destinationPath.is_dir():
      raise InstallError(f'目标路径类型冲突: {destinationPath}')
    if sourcePath.is_file() and not destinationPath.is_file():
      raise InstallError(f'目标路径类型冲突: {destinationPath}')
  versionPath = targetRoot / '.template-version'
  if _pathExists(versionPath) and not versionPath.is_file():
    raise InstallError(f'目标版本元数据不是普通文件: {versionPath}')


def _reserveTemporaryPath(destinationPath: Path, purpose: str) -> Path:
  """功能：在目标同目录用独占创建预留随机临时文件名。

  入参：destinationPath 为最终目标，purpose 为临时文件用途标签。
  返回值：当前调用独占拥有的空临时文件路径。
  边界情况：随机名冲突时最多重试 100 个带递增后缀的候选，耗尽后抛出 FileExistsError。
  """
  randomToken = uuid.uuid4().hex
  for attempt in range(100):
    suffix = '' if attempt == 0 else f'-{attempt}'
    temporaryPath = destinationPath.with_name(
      f'.{destinationPath.name}.{purpose}-{randomToken}{suffix}.tmp'
    )
    try:
      with temporaryPath.open('xb'):
        pass
      return temporaryPath
    except FileExistsError:
      continue
  raise FileExistsError(f'无法预留临时文件: {destinationPath}')


def _copyFileAtomically(sourcePath: Path, destinationPath: Path) -> OwnedPath:
  """功能：在目标同目录暂存复制，再用硬链接原子且不可覆盖地落位普通文件。

  入参：sourcePath 为源文件，destinationPath 为当前不存在的目标文件。
  返回值：最终目标文件的所有权记录。
  边界情况：目标被并发创建时 os.link 以 FileExistsError 失败；临时文件始终清理且不触碰既有目标。
  """
  temporaryPath = _reserveTemporaryPath(destinationPath, 'copy')
  try:
    shutil.copy2(sourcePath, temporaryPath)
    temporaryOwnedPath = _captureOwnedPath(temporaryPath)
    destinationOwnedPath = _moveOwnedPath(temporaryOwnedPath, destinationPath)
    try:
      os.link(temporaryPath, destinationPath)
      return destinationOwnedPath
    except OSError as linkError:
      unsupportedErrors = {
        errno.EACCES,
        errno.EINVAL,
        errno.ENOTSUP,
        errno.EOPNOTSUPP,
        errno.EPERM,
        errno.EXDEV,
      }
      if linkError.errno not in unsupportedErrors:
        raise
      return _copyTemporaryFileExclusive(temporaryPath, destinationPath)
  finally:
    if _pathExists(temporaryPath):
      temporaryPath.unlink(missing_ok=True)


def _copyTemporaryFileExclusive(temporaryPath: Path, destinationPath: Path) -> OwnedPath:
  """功能：硬链接不可用时用 O_CREAT|O_EXCL 独占创建并复制临时文件内容。

  入参：temporaryPath 为已完整写好的同目录临时文件，destinationPath 为最终目标。
  返回值：通过目标文件描述符 fstat 捕获的所有权记录。
  边界情况：目标并发出现时 O_EXCL 失败且绝不覆盖；复制中断只删除身份仍匹配的本次文件。
  """
  openFlags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_BINARY', 0)
  destinationDescriptor = os.open(destinationPath, openFlags, 0o600)
  initialOwnedPath = _ownedPathFromStat(
    destinationPath,
    os.fstat(destinationDescriptor),
  )
  createdOwnedPath: OwnedPath | None = None
  try:
    with os.fdopen(destinationDescriptor, 'wb') as destinationFile:
      destinationDescriptor = -1
      with temporaryPath.open('rb') as sourceFile:
        shutil.copyfileobj(sourceFile, destinationFile)
      destinationFile.flush()
      pathStat = os.fstat(destinationFile.fileno())
      createdOwnedPath = _ownedPathFromStat(destinationPath, pathStat)
    return createdOwnedPath
  except BaseException:
    if destinationDescriptor >= 0:
      os.close(destinationDescriptor)
    cleanupOwnedPath = createdOwnedPath or initialOwnedPath
    if _matchingIdentityStat(cleanupOwnedPath) is not None:
      destinationPath.unlink(missing_ok=True)
    raise


def _writeVersionAtomically(versionPath: Path, templateVersion: str) -> OwnedPath:
  """功能：在同目录写入临时版本文件并原子替换正式元数据。

  入参：versionPath 为元数据路径，templateVersion 为已校验版本号。
  返回值：替换完成后版本元数据的所有权记录。
  边界情况：写入或替换失败时清理临时文件，既有元数据由调用方从备份恢复。
  """
  temporaryPath = _reserveTemporaryPath(versionPath, 'write')
  try:
    temporaryPath.write_bytes(f'{templateVersion}\n'.encode('utf-8'))
    temporaryOwnedPath = _captureOwnedPath(temporaryPath)
    versionOwnedPath = _moveOwnedPath(temporaryOwnedPath, versionPath)
    os.replace(temporaryPath, versionPath)
    return versionOwnedPath
  finally:
    if _pathExists(temporaryPath):
      temporaryPath.unlink(missing_ok=True)


def _writeNewVersionNoClobber(versionPath: Path, templateVersion: str) -> OwnedPath:
  """功能：为原本无版本元数据的目标不可覆盖地写入新版本文件。

  入参：versionPath 为应当不存在的元数据路径，templateVersion 为版本号。
  返回值：新版本文件的所有权记录。
  边界情况：并发出现同名文件时抛出 FileExistsError，不覆盖对方内容。
  """
  temporaryPath = _reserveTemporaryPath(versionPath, 'write')
  try:
    temporaryPath.write_bytes(f'{templateVersion}\n'.encode('utf-8'))
    temporaryOwnedPath = _captureOwnedPath(temporaryPath)
    versionOwnedPath = _moveOwnedPath(temporaryOwnedPath, versionPath)
    try:
      os.link(temporaryPath, versionPath)
      return versionOwnedPath
    except OSError as linkError:
      unsupportedErrors = {
        errno.EACCES,
        errno.EINVAL,
        errno.ENOTSUP,
        errno.EOPNOTSUPP,
        errno.EPERM,
        errno.EXDEV,
      }
      if linkError.errno not in unsupportedErrors:
        raise
      return _copyTemporaryFileExclusive(temporaryPath, versionPath)
  finally:
    if _pathExists(temporaryPath):
      temporaryPath.unlink(missing_ok=True)


def _createMetadataBackup(versionPath: Path) -> OwnedPath | None:
  """功能：为现有模板版本元数据创建同目录、不可覆盖的恢复副本。

  入参：versionPath 为可能存在的 `.template-version` 文件。
  返回值：元数据存在时返回最终备份的所有权记录，不存在时返回 None。
  边界情况：备份通过随机临时文件原子落位；失败时不留下半成品备份。
  """
  if not _pathExists(versionPath):
    return None
  randomToken = uuid.uuid4().hex
  for attempt in range(100):
    suffix = '' if attempt == 0 else f'-{attempt}'
    backupPath = versionPath.with_name(
      f'{versionPath.name}.backup-{randomToken}{suffix}'
    )
    try:
      return _copyFileAtomically(versionPath, backupPath)
    except FileExistsError:
      continue
  raise FileExistsError(f'无法创建不可覆盖版本备份: {versionPath}')


def _restoreMetadata(
  versionPath: Path,
  backupOwnedPath: OwnedPath | None,
  existedBefore: bool,
  versionOwnedPath: OwnedPath,
) -> None:
  """功能：更新失败后按原始存在状态恢复版本元数据字节。

  入参：versionPath 为正式元数据，backupOwnedPath 为恢复副本身份，existedBefore 表示更新前是否存在，versionOwnedPath 为本次写入身份。
  返回值：无。
  边界情况：原文件不存在时删除本次新建元数据；备份缺失时保留现场供诊断。
  """
  if not _stillOwnsPath(versionOwnedPath):
    raise OSError(f'版本元数据已被并发修改，拒绝覆盖恢复: {versionPath}')
  if not existedBefore:
    versionPath.unlink()
    return
  if backupOwnedPath is None or not _stillOwnsPath(backupOwnedPath):
    backupPath = backupOwnedPath.path if backupOwnedPath is not None else None
    raise OSError(f'版本元数据备份不可用或已被修改: {backupPath}')
  backupPath = backupOwnedPath.path
  temporaryPath = _reserveTemporaryPath(versionPath, 'restore')
  try:
    temporaryPath.write_bytes(backupPath.read_bytes())
    os.replace(temporaryPath, versionPath)
  finally:
    if _pathExists(temporaryPath):
      temporaryPath.unlink(missing_ok=True)


def _reserveStagingDirectory(targetRoot: Path) -> tuple[Path, OwnedPath]:
  """功能：在安装目标同级独占创建 Windows 首次安装 staging 目录。

  入参：targetRoot 为最终安装目录。
  返回值：staging 路径及其所有权记录。
  边界情况：随机名冲突时尝试最多 100 个递增候选，始终使用独占 mkdir。
  """
  randomToken = uuid.uuid4().hex
  for attempt in range(100):
    suffix = '' if attempt == 0 else f'-{attempt}'
    stagingRoot = targetRoot.with_name(
      f'.{targetRoot.name}.install-{randomToken}{suffix}'
    )
    try:
      stagingRoot.mkdir()
      return stagingRoot, _captureOwnedPath(stagingRoot)
    except FileExistsError:
      continue
  raise FileExistsError(f'无法创建安装 staging 目录: {targetRoot}')


def _installNewTemplateOnWindows(
  targetRoot: Path,
  sourceRoot: Path,
  sourcePaths: tuple[Path, ...],
  templateVersion: str,
  createdParents: list[OwnedPath],
) -> InstallReport:
  """功能：在 Windows 同级 staging 构造完整模板，再以不可覆盖目录 rename 落位。

  入参：targetRoot 为最终目标，sourceRoot/sourcePaths 为模板，templateVersion 为版本，createdParents 为本次父目录。
  返回值：成功安装报告。
  边界情况：Windows rename 遇到竞争创建的目标会失败；回滚只清理由当前调用仍拥有的 staging 对象。
  """
  stagingRoot, stagingOwnedPath = _reserveStagingDirectory(targetRoot)
  createdPaths = [stagingOwnedPath]
  try:
    createdPaths.extend(_copyTemplateTree(sourceRoot, stagingRoot, sourcePaths))
    createdPaths.append(
      _writeNewVersionNoClobber(stagingRoot / '.template-version', templateVersion)
    )
    reportPaths = [targetRoot]
    reportPaths.extend(
      targetRoot / ownedPath.path.relative_to(stagingRoot)
      for ownedPath in createdPaths[1:]
    )
    os.rename(stagingRoot, targetRoot)
    return InstallReport(
      targetRoot=targetRoot,
      createdPaths=tuple(reportPaths),
      backupPath=None,
      templateVersion=templateVersion,
    )
  except (OSError, UnicodeError) as error:
    retainedPaths = _removeCreatedPaths(createdPaths)
    _removeEmptyParents(createdParents)
    retainedContext = (
      '；并发接管路径已保留: ' + ', '.join(str(path) for path in retainedPaths)
      if retainedPaths else ''
    )
    raise InstallError(
      f'安装失败，已撤销仍由安装器持有的 staging 路径: {stagingRoot}{retainedContext}'
    ) from error


def _installNewTemplate(
  targetRoot: Path,
  sourceRoot: Path,
  sourcePaths: tuple[Path, ...],
  templateVersion: str,
) -> InstallReport:
  """功能：通过独占创建目标根保留名称，再以不可覆盖方式填充完整模板。

  入参：targetRoot 为不存在的目标，sourceRoot/sourcePaths 为安全模板，templateVersion 为版本号。
  返回值：包含全部新建目标路径的 InstallReport。
  边界情况：目标创建后安装过程可能短暂可见；失败时仅按身份删除当前调用仍拥有的文件和空目录。
  """
  createdParents: list[OwnedPath] = []
  createdPaths: list[OwnedPath] = []
  try:
    createdParents = _createMissingParents(targetRoot.parent)
    if os.name == 'nt':
      return _installNewTemplateOnWindows(
        targetRoot,
        sourceRoot,
        sourcePaths,
        templateVersion,
        createdParents,
      )
    targetRoot.mkdir()
    createdPaths.append(_captureOwnedPath(targetRoot))
    createdPaths.extend(_copyTemplateTree(sourceRoot, targetRoot, sourcePaths))
    createdPaths.append(
      _writeNewVersionNoClobber(targetRoot / '.template-version', templateVersion)
    )
    return InstallReport(
      targetRoot=targetRoot,
      createdPaths=tuple(ownedPath.path for ownedPath in createdPaths),
      backupPath=None,
      templateVersion=templateVersion,
    )
  except (OSError, UnicodeError) as error:
    retainedPaths = _removeCreatedPaths(createdPaths)
    _removeEmptyParents(createdParents)
    retainedContext = (
      '；并发接管路径已保留: ' + ', '.join(str(path) for path in retainedPaths)
      if retainedPaths else ''
    )
    raise InstallError(
      f'安装失败，已撤销仍由安装器持有的路径: {targetRoot}{retainedContext}'
    ) from error


def _updateTemplate(
  targetRoot: Path,
  sourceRoot: Path,
  sourcePaths: tuple[Path, ...],
  templateVersion: str,
) -> InstallReport:
  """功能：仅补入目标缺失的模板路径，并可恢复地更新版本元数据。

  入参：targetRoot 为现有安装，sourceRoot/sourcePaths 为安全模板，templateVersion 为版本号。
  返回值：包含新增路径、备份路径和版本的 InstallReport。
  边界情况：失败时撤销本次新增路径、恢复旧元数据并保留备份供人工恢复。
  """
  _preflightUpdateTarget(targetRoot, sourceRoot, sourcePaths)
  versionPath = targetRoot / '.template-version'
  versionExisted = _pathExists(versionPath)
  backupOwnedPath: OwnedPath | None = None
  versionOwnedPath: OwnedPath | None = None
  createdPaths: list[OwnedPath] = []
  try:
    backupOwnedPath = _createMetadataBackup(versionPath)
    if versionExisted:
      versionOwnedPath = _writeVersionAtomically(versionPath, templateVersion)
    else:
      versionOwnedPath = _writeNewVersionNoClobber(versionPath, templateVersion)
    directoryPaths = [path for path in sourcePaths if path.is_dir()]
    filePaths = [path for path in sourcePaths if path.is_file()]
    for sourcePath in directoryPaths:
      destinationPath = targetRoot / sourcePath.relative_to(sourceRoot)
      if not _pathExists(destinationPath):
        destinationPath.mkdir()
        createdPaths.append(_captureOwnedPath(destinationPath))
    for sourcePath in filePaths:
      destinationPath = targetRoot / sourcePath.relative_to(sourceRoot)
      if not _pathExists(destinationPath):
        createdPaths.append(_copyFileAtomically(sourcePath, destinationPath))
    reportPaths = [ownedPath.path for ownedPath in createdPaths]
    if not versionExisted:
      reportPaths.append(versionPath)
    return InstallReport(
      targetRoot=targetRoot,
      createdPaths=tuple(reportPaths),
      backupPath=backupOwnedPath.path if backupOwnedPath is not None else None,
      templateVersion=templateVersion,
    )
  except (OSError, UnicodeError) as error:
    retainedPaths = _removeCreatedPaths(createdPaths)
    if versionOwnedPath is None:
      restoreStatus = '版本元数据未替换'
    else:
      try:
        _restoreMetadata(
          versionPath,
          backupOwnedPath,
          versionExisted,
          versionOwnedPath,
        )
        restoreStatus = '版本元数据恢复成功'
      except (OSError, UnicodeError) as restoreError:
        restoreStatus = f'版本元数据恢复失败: {restoreError}'
    backupContext = (
      str(backupOwnedPath.path)
      if backupOwnedPath is not None else '无可用版本备份'
    )
    retainedContext = (
      '；并发接管路径已保留: ' + ', '.join(str(path) for path in retainedPaths)
      if retainedPaths else ''
    )
    raise InstallError(
      f'更新失败；{restoreStatus}；版本备份: {backupContext}{retainedContext}'
    ) from error


def installTemplate(
  repositoryRoot: Path,
  targetRoot: Path,
  update: bool = False,
) -> InstallReport:
  """功能：从仓库公开模板执行首次安装或仅补缺失项的安全更新。

  入参：repositoryRoot 为仓库根，targetRoot 为安装目录，update 指定是否更新现有安装。
  返回值：包含目标、新增路径、备份位置和模板版本的不可变报告。
  边界情况：首次安装拒绝现存目标，更新拒绝缺失目标；所有链接、重解析点和类型冲突均在写入前失败。
  """
  repositoryRoot = Path(repositoryRoot)
  targetRoot = Path(targetRoot)
  sourceRoot = repositoryRoot / 'memory'
  _assertDisjointTrees(sourceRoot, Path(targetRoot))
  _, sourcePaths = _validateSourceTemplate(sourceRoot)
  selectedPaths = _selectTemplatePaths(sourceRoot, sourcePaths)
  templateVersion = _readTemplateVersion(repositoryRoot)
  _assertSafeAncestors(targetRoot, '目标路径')
  targetExists = _pathExists(targetRoot)
  if update:
    if not targetExists:
      raise InstallError(f'更新目标不存在: {targetRoot}')
    return _updateTemplate(
      targetRoot,
      sourceRoot,
      selectedPaths,
      templateVersion,
    )
  if targetExists:
    raise InstallError(f'安装目标已存在，未执行覆盖: {targetRoot}')
  return _installNewTemplate(
    targetRoot,
    sourceRoot,
    selectedPaths,
    templateVersion,
  )
