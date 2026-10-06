$cliArguments = @($args)
$pythonCommand = $null
$pythonPrefix = @()

foreach ($candidate in @('python', 'py')) {
  if (-not (Get-Command $candidate -ErrorAction SilentlyContinue)) {
    continue
  }
  $candidatePrefix = if ($candidate -eq 'py') { @('-3') } else { @() }
  & $candidate @candidatePrefix -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>$null
  if ($LASTEXITCODE -eq 0) {
    $pythonCommand = $candidate
    $pythonPrefix = $candidatePrefix
    break
  }
}

if ($null -eq $pythonCommand) {
  [Console]::Error.WriteLine('Python 3.11 or newer is required.')
  exit 2
}

Push-Location -LiteralPath $PSScriptRoot
try {
  & $pythonCommand @pythonPrefix -m codex_memory @cliArguments
  $commandExitCode = $LASTEXITCODE
} finally {
  Pop-Location
}
exit $commandExitCode
