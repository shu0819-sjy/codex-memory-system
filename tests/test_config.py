import json
import re
import tempfile
import unittest
from pathlib import Path

from codex_memory.config import ConfigError, MemoryConfig, loadConfig


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


class ConfigTestCase(unittest.TestCase):
  """功能：验证配置加载和输入校验契约。

  入参：无，unittest 负责构造测试实例。
  返回值：无，通过断言报告结果。
  边界情况：每个测试使用独立临时目录，不读写用户记忆。
  """

  def setUp(self) -> None:
    """功能：为单个测试创建独立的临时记忆目录。

    入参：无。
    返回值：无。
    边界情况：目录在测试结束时自动清理。
    """
    self.tempDirectory = tempfile.TemporaryDirectory()
    self.memoryRoot = Path(self.tempDirectory.name)

  def tearDown(self) -> None:
    """功能：清理单个测试创建的临时目录。

    入参：无。
    返回值：无。
    边界情况：即使目录内容为空也可安全调用。
    """
    self.tempDirectory.cleanup()

  def writeConfig(self, config: object) -> None:
    """功能：将测试配置以 UTF-8 JSON 写入临时记忆目录。

    入参：config 为可由 json.dumps 序列化的测试对象。
    返回值：无。
    边界情况：不处理不可序列化对象，这不属于被测边界。
    """
    configPath = self.memoryRoot / 'config.json'
    configPath.write_text(
      json.dumps(config, ensure_ascii=False),
      encoding='utf-8',
    )

  def validConfig(self) -> dict[str, object]:
    """功能：返回手工编写的有效配置样例。

    入参：无。
    返回值：包含全部必需字段的可变字典。
    边界情况：返回新字典，测试可独立修改而不相互影响。
    """
    return {
      'categories': ['study', '项目'],
      'hotLimitBytes': 12000,
      'spaceLimitBytes': 5000,
      'rulesLimitBytes': 4500,
      'activeTaskLimit': 4,
      'staleDays': 45,
      'archiveNamePattern': r'^\d{4}-\d{2}-.+\.md$',
      'privacyPatterns': [r'token', r'password'],
    }

  def testLoadsStandardJsonAsFrozenConfig(self) -> None:
    """功能：验证标准 JSON 被加载为不可变配置。

    入参：无。
    返回值：无，通过断言报告结果。
    边界情况：对加载后的字段赋值必须失败。
    """
    self.writeConfig(self.validConfig())

    config = loadConfig(self.memoryRoot)

    self.assertEqual(
      config,
      MemoryConfig(
        categories=('study', '项目'),
        hotLimitBytes=12000,
        spaceLimitBytes=5000,
        rulesLimitBytes=4500,
        activeTaskLimit=4,
        staleDays=45,
        archiveNamePattern=r'^\d{4}-\d{2}-.+\.md$',
        privacyPatterns=(r'token', r'password'),
      ),
    )
    with self.assertRaises(AttributeError):
      config.staleDays = 46

  def testMissingFileUsesPublicDefaults(self) -> None:
    """功能：验证配置文件缺失时返回公开默认值。

    入参：无。
    返回值：无，通过断言报告结果。
    边界情况：临时目录中不创建 config.json。
    """
    config = loadConfig(self.memoryRoot)

    self.assertEqual(config.categories, DEFAULT_CATEGORIES)
    self.assertEqual(config.hotLimitBytes, 10 * 1024)
    self.assertEqual(config.spaceLimitBytes, 4 * 1024)
    self.assertEqual(config.rulesLimitBytes, 4 * 1024)
    self.assertEqual(config.activeTaskLimit, 3)
    self.assertEqual(config.staleDays, 30)
    self.assertEqual(config.archiveNamePattern, r'^\d{4}-\d{2}-.+\.md$')
    self.assertEqual(config.privacyPatterns, DEFAULT_PRIVACY_PATTERNS)

  def testRejectsInvalidJson(self) -> None:
    """功能：验证损坏的 JSON 配置被拒绝。

    入参：无。
    返回值：无，通过异常断言报告结果。
    边界情况：使用未闭合 JSON 对象触发解析失败。
    """
    (self.memoryRoot / 'config.json').write_text('{', encoding='utf-8')

    with self.assertRaises(ConfigError):
      loadConfig(self.memoryRoot)

  def testRejectsUnknownField(self) -> None:
    """功能：验证未知配置字段被拒绝。

    入参：无。
    返回值：无，通过异常断言报告结果。
    边界情况：使用拼写错误的阈值字段模拟真实误配。
    """
    config = self.validConfig()
    config['hotLimitByte'] = config.pop('hotLimitBytes')
    self.writeConfig(config)

    with self.assertRaises(ConfigError):
      loadConfig(self.memoryRoot)

  def testRejectsMissingRequiredField(self) -> None:
    """功能：验证缺失必需配置字段时被拒绝。

    入参：无。
    返回值：无，通过异常断言报告结果。
    边界情况：删除 staleDays，其余字段保持有效。
    """
    config = self.validConfig()
    del config['staleDays']
    self.writeConfig(config)

    with self.assertRaises(ConfigError):
      loadConfig(self.memoryRoot)

  def testRejectsWrongFieldTypes(self) -> None:
    """功能：验证各类配置字段的错误类型被拒绝。

    入参：无。
    返回值：无，通过子测试的异常断言报告结果。
    边界情况：显式覆盖 bool 作为 int 子类的特殊情况。
    """
    invalidValues = (
      ('categories', 'study'),
      ('categories', ['study', 1]),
      ('hotLimitBytes', '12000'),
      ('activeTaskLimit', True),
      ('archiveNamePattern', 1),
      ('privacyPatterns', 'token'),
      ('privacyPatterns', ['token', 1]),
    )

    for fieldName, invalidValue in invalidValues:
      with self.subTest(fieldName=fieldName, invalidValue=invalidValue):
        config = self.validConfig()
        config[fieldName] = invalidValue
        self.writeConfig(config)
        with self.assertRaises(ConfigError):
          loadConfig(self.memoryRoot)

  def testRejectsNegativeThresholds(self) -> None:
    """功能：验证所有数值阈值都拒绝负数。

    入参：无。
    返回值：无，通过子测试的异常断言报告结果。
    边界情况：对每个阈值单独传入 -1，其余字段保持有效。
    """
    for fieldName in (
      'hotLimitBytes',
      'spaceLimitBytes',
      'rulesLimitBytes',
      'activeTaskLimit',
      'staleDays',
    ):
      with self.subTest(fieldName=fieldName):
        config = self.validConfig()
        config[fieldName] = -1
        self.writeConfig(config)
        with self.assertRaises(ConfigError):
          loadConfig(self.memoryRoot)

  def testRejectsInvalidArchiveRegularExpression(self) -> None:
    """功能：验证无法编译的归档正则被拒绝。

    入参：无。
    返回值：无，通过异常断言报告结果。
    边界情况：使用未闭合字符类触发正则编译失败。
    """
    config = self.validConfig()
    config['archiveNamePattern'] = '['
    self.writeConfig(config)

    with self.assertRaises(ConfigError):
      loadConfig(self.memoryRoot)

  def testRejectsUnsafeCategoryNames(self) -> None:
    """功能：验证跨平台不安全的分类文件名全部被拒绝。

    入参：无。
    返回值：无，通过子测试的异常断言报告结果。
    边界情况：覆盖空白、路径分隔符、控制字符、Windows 非法字符、尾随点/空格和含扩展名的保留设备名。
    """
    unsafeNames = (
      '',
      '   ',
      '.',
      '..',
      'nested/name',
      r'nested\name',
      'line\nbreak',
      'name<',
      'name>',
      'name:',
      'name"',
      'name|',
      'name?',
      'name*',
      'name.',
      'name ',
      'CON',
      'con.txt',
      'PRN',
      'prn.log',
      'AUX',
      'aux.md',
      'NUL',
      'nul.txt',
      'COM1',
      'COM2.txt',
      'COM3',
      'COM4.txt',
      'COM5',
      'COM6.txt',
      'COM7',
      'COM8.txt',
      'COM9',
      'com9.log',
      'COM¹',
      'com².txt',
      'Com³.Log',
      'LPT1',
      'LPT2.txt',
      'LPT3',
      'LPT4.txt',
      'LPT5',
      'LPT6.txt',
      'LPT7',
      'LPT8.txt',
      'LPT9',
      'lpt9.log',
      'LPT¹',
      'lpt².txt',
      'Lpt³.Log',
    )

    for unsafeName in unsafeNames:
      with self.subTest(unsafeName=unsafeName):
        config = self.validConfig()
        config['categories'] = [unsafeName]
        self.writeConfig(config)
        with self.assertRaises(ConfigError):
          loadConfig(self.memoryRoot)

  def testArchivePatternIsUsableAfterLoading(self) -> None:
    """功能：验证加载后的归档正则可直接匹配合法文件名。

    入参：无。
    返回值：无，通过正则匹配断言报告结果。
    边界情况：使用最小合法年月与主题文件名。
    """
    self.writeConfig(self.validConfig())

    config = loadConfig(self.memoryRoot)

    self.assertIsNotNone(re.fullmatch(config.archiveNamePattern, '2026-10-topic.md'))


if __name__ == '__main__':
  unittest.main()
