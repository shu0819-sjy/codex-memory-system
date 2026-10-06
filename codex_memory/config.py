"""JSON 配置加载、默认值与输入校验。"""

import json
import re
import unicodedata
from dataclasses import dataclass, fields
from pathlib import Path


DEFAULT_CATEGORIES = (
  '学业',
  '出国申请',
  '竞赛',
  '项目',
  '工具与环境',
  '人设',
  '任务要求',
)
DEFAULT_PRIVACY_PATTERNS = (
  'api_key=',
  'access_token=',
  'password=',
  'secret=',
  '-----BEGIN PRIVATE KEY-----',
  'C:\\Users\\',
  'D:\\',
  '/Users/',
  '/home/',
)
DEFAULT_ARCHIVE_NAME_PATTERN = r'^\d{4}-\d{2}-.+\.md$'
WINDOWS_INVALID_FILENAME_CHARACTERS = frozenset('<>:"|?*')
WINDOWS_RESERVED_DEVICE_NAMES = frozenset((
  'CON',
  'PRN',
  'AUX',
  'NUL',
  'COM1',
  'COM2',
  'COM3',
  'COM4',
  'COM5',
  'COM6',
  'COM7',
  'COM8',
  'COM9',
  'COM¹',
  'COM²',
  'COM³',
  'LPT1',
  'LPT2',
  'LPT3',
  'LPT4',
  'LPT5',
  'LPT6',
  'LPT7',
  'LPT8',
  'LPT9',
  'LPT¹',
  'LPT²',
  'LPT³',
))


class ConfigError(ValueError):
  """表示配置文件结构、类型或取值无效。"""


@dataclass(frozen=True)
class MemoryConfig:
  """表示经严格校验且不可变的记忆系统配置。"""

  categories: tuple[str, ...]
  hotLimitBytes: int
  spaceLimitBytes: int
  rulesLimitBytes: int
  activeTaskLimit: int
  staleDays: int
  archiveNamePattern: str
  privacyPatterns: tuple[str, ...]


DEFAULT_CONFIG = MemoryConfig(
  categories=DEFAULT_CATEGORIES,
  hotLimitBytes=10240,
  spaceLimitBytes=4096,
  rulesLimitBytes=4096,
  activeTaskLimit=3,
  staleDays=30,
  archiveNamePattern=DEFAULT_ARCHIVE_NAME_PATTERN,
  privacyPatterns=DEFAULT_PRIVACY_PATTERNS,
)


def _validateStringList(value: object, fieldName: str) -> tuple[str, ...]:
  """功能：校验 JSON 字符串数组并转换为不可变元组。

  入参：value 为待校验值，fieldName 为用于定位错误的字段名。
  返回值：包含原字符串的元组。
  边界情况：允许空数组，但拒绝非数组或含非字符串元素的值。
  """
  if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
    raise ConfigError(f'{fieldName} 必须是字符串数组')
  return tuple(value)


def _validateNonNegativeInteger(value: object, fieldName: str) -> int:
  """功能：校验阈值是非负整数。

  入参：value 为待校验值，fieldName 为用于定位错误的字段名。
  返回值：通过校验的整数。
  边界情况：允许 0；因为 bool 是 int 的子类，需显式拒绝。
  """
  if isinstance(value, bool) or not isinstance(value, int) or value < 0:
    raise ConfigError(f'{fieldName} 必须是非负整数')
  return value


def _validateCategory(category: str) -> None:
  """功能：校验分类名可安全作为单一文件名。

  入参：category 为待校验的分类名。
  返回值：无，校验失败时抛出 ConfigError。
  边界情况：拒绝空白、相对路径标记、非法字符、尾随点/空格和带扩展名的 Windows 保留设备名。
  """
  hasControlCharacter = any(
    unicodedata.category(character) == 'Cc' for character in category
  )
  hasInvalidWindowsCharacter = any(
    character in WINDOWS_INVALID_FILENAME_CHARACTERS for character in category
  )
  deviceName = category.partition('.')[0].upper()
  if (
    not category.strip()
    or category in ('.', '..')
    or '/' in category
    or '\\' in category
    or hasControlCharacter
    or hasInvalidWindowsCharacter
    or category.endswith(('.', ' '))
    or deviceName in WINDOWS_RESERVED_DEVICE_NAMES
  ):
    raise ConfigError('categories 包含不安全的分类名')


def _parseConfig(rawConfig: object) -> MemoryConfig:
  """功能：将已解析的 JSON 对象校验并转换为 MemoryConfig。

  入参：rawConfig 为 json.load 返回的任意 JSON 值。
  返回值：不可变的 MemoryConfig 实例。
  边界情况：拒绝非对象根值、未知字段、缺失字段及任何无效字段值。
  """
  if not isinstance(rawConfig, dict):
    raise ConfigError('配置根节点必须是 JSON 对象')

  expectedFields = {field.name for field in fields(MemoryConfig)}
  actualFields = set(rawConfig)
  if actualFields != expectedFields:
    raise ConfigError('配置字段缺失或包含未知字段')

  categories = _validateStringList(rawConfig['categories'], 'categories')
  for category in categories:
    _validateCategory(category)

  integerFields = (
    'hotLimitBytes',
    'spaceLimitBytes',
    'rulesLimitBytes',
    'activeTaskLimit',
    'staleDays',
  )
  validatedIntegers = {
    fieldName: _validateNonNegativeInteger(rawConfig[fieldName], fieldName)
    for fieldName in integerFields
  }

  archiveNamePattern = rawConfig['archiveNamePattern']
  if not isinstance(archiveNamePattern, str):
    raise ConfigError('archiveNamePattern 必须是字符串')
  try:
    re.compile(archiveNamePattern)
  except re.error as error:
    raise ConfigError('archiveNamePattern 不是有效正则表达式') from error

  privacyPatterns = _validateStringList(
    rawConfig['privacyPatterns'],
    'privacyPatterns',
  )
  return MemoryConfig(
    categories=categories,
    archiveNamePattern=archiveNamePattern,
    privacyPatterns=privacyPatterns,
    **validatedIntegers,
  )


def loadConfig(memoryRoot: Path) -> MemoryConfig:
  """功能：加载并校验记忆根目录中的 config.json。

  入参：memoryRoot 为包含可选 config.json 的记忆根目录。
  返回值：文件存在时返回经校验的配置，缺失时返回公开模板默认值。
  边界情况：JSON 损坏或不符合契约时抛出 ConfigError，不修改任何文件。
  """
  configPath = memoryRoot / 'config.json'
  if not configPath.exists():
    return DEFAULT_CONFIG

  try:
    with configPath.open('r', encoding='utf-8') as configFile:
      rawConfig = json.load(configFile)
  except (OSError, UnicodeError, json.JSONDecodeError) as error:
    raise ConfigError('无法读取有效的 config.json') from error
  return _parseConfig(rawConfig)

