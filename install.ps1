param(
  [string]$InstallRoot = (Join-Path ([Environment]::GetFolderPath('UserProfile')) '.codex-memory'),
  [switch]$Force
)

$ErrorActionPreference = 'Stop'
$sourceRoot = Join-Path $PSScriptRoot 'memory'
$marker = Join-Path $InstallRoot '.codex-memory-template'
$backup = $null

if (-not (Test-Path -LiteralPath (Join-Path $sourceRoot 'MEMORY.md'))) {
  throw 'Template is incomplete: memory/MEMORY.md was not found.'
}

if ((Test-Path -LiteralPath $InstallRoot) -and -not $Force) {
  throw "Target already exists: $InstallRoot. Re-run with -Force to back it up and replace it."
}

if (Test-Path -LiteralPath $InstallRoot) {
  $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
  $backup = "$InstallRoot.bak-$stamp"
  Move-Item -LiteralPath $InstallRoot -Destination $backup
  Write-Output "Backed up existing directory: $backup"
}

try {
  New-Item -ItemType Directory -Path $InstallRoot -Force | Out-Null
  Get-ChildItem -LiteralPath $sourceRoot -Force | Copy-Item -Destination $InstallRoot -Recurse -Force
  [IO.File]::WriteAllText($marker, 'Installed from codex-memory-system template.', [Text.UTF8Encoding]::new($false))

  $checker = Join-Path $InstallRoot 'check-memory.ps1'
  powershell -NoProfile -ExecutionPolicy Bypass -File $checker
  if ($LASTEXITCODE -ne 0) {
    throw "Post-install check failed with exit code: $LASTEXITCODE"
  }
} catch {
  if (Test-Path -LiteralPath $InstallRoot) {
    Remove-Item -LiteralPath $InstallRoot -Recurse -Force
  }
  if ($backup -and (Test-Path -LiteralPath $backup)) {
    Move-Item -LiteralPath $backup -Destination $InstallRoot
    Write-Output "Restored previous installation: $InstallRoot"
  }
  throw
}

Write-Output "Installation complete: $InstallRoot"
Write-Output 'Next: connect MEMORY.md and RULES.md to your AGENTS.md or workflow.'
