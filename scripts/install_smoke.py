"""验证跨平台安装器、更新流程和平台包装器。"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path


def runCommand(command: list[str], repositoryRoot: Path) -> None:
  """功能：在仓库根目录执行命令并在失败时保留完整上下文。

  入参：command 为不经过 shell 拼接的参数列表；repositoryRoot 为工作目录。
  返回值：命令成功时无返回值。
  边界情况：命令非零退出时抛出带 stdout/stderr 的 RuntimeError。
  """
  result = subprocess.run(
    command,
    cwd=repositoryRoot,
    capture_output=True,
    text=True,
    encoding='utf-8',
    errors='replace',
    check=False,
  )
  if result.returncode == 0:
    return
  details = [f'命令退出码: {result.returncode}', f'命令: {command}']
  if result.stdout:
    details.append(f'stdout:\n{result.stdout}')
  if result.stderr:
    details.append(f'stderr:\n{result.stderr}')
  raise RuntimeError('\n'.join(details))


def buildWrapperCommand(repositoryRoot: Path) -> list[str]:
  """功能：选择当前操作系统可用的仓库包装器。

  入参：repositoryRoot 为仓库根目录。
  返回值：包装器命令前缀，不包含子命令参数。
  边界情况：仅支持 Windows PowerShell 和 POSIX sh；未知系统抛出 RuntimeError。
  """
  if os.name == 'nt':
    return [
      'powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass',
      '-File', str(repositoryRoot / 'codex-memory.ps1'),
    ]
  if os.name == 'posix':
    return ['sh', str(repositoryRoot / 'codex-memory')]
  raise RuntimeError(f'不支持的操作系统: {os.name}')


def main() -> int:
  """功能：在隔离临时目录完成安装、更新、检查和包装器验证。

  入参：无，参数由脚本固定定义以保证 CI 与本地行为一致。
  返回值：全部步骤成功时返回 0；失败时由异常终止并返回非零状态。
  边界情况：临时目录自动清理，不读取或修改用户默认记忆目录。
  """
  repositoryRoot = Path(__file__).resolve().parents[1]
  commandPrefix = [sys.executable, '-m', 'codex_memory']
  with tempfile.TemporaryDirectory(prefix='codex-memory-smoke-') as temporaryPath:
    memoryRoot = Path(temporaryPath) / 'memory'
    runCommand(
      commandPrefix + ['install', '--non-interactive', '--root', str(memoryRoot)],
      repositoryRoot,
    )
    runCommand(
      commandPrefix + [
        'install', '--update', '--non-interactive', '--root', str(memoryRoot),
      ],
      repositoryRoot,
    )
    runCommand(commandPrefix + ['check', '--root', str(memoryRoot)], repositoryRoot)
    runCommand(
      buildWrapperCommand(repositoryRoot) + ['check', '--root', str(memoryRoot)],
      repositoryRoot,
    )
  print('安装 smoke 通过：install、update、check 和平台包装器均成功。')
  return 0


if __name__ == '__main__':
  raise SystemExit(main())
