# Codex memory v3.2 integrity check (read-only)
# Structure + content-level checks
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File .\memory\check-memory.ps1
# Exit: 0=PASS 1=FAIL

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$spaces = @('学业','出国申请','竞赛','项目','工具与环境','人设','任务要求')
$fail = New-Object System.Collections.Generic.List[string]
$warn = New-Object System.Collections.Generic.List[string]

function Test-Utf8NoBom([string]$path) {
  $b = [IO.File]::ReadAllBytes($path)
  if ($b.Length -ge 3 -and $b[0] -eq 0xEF -and $b[1] -eq 0xBB -and $b[2] -eq 0xBF) { return $false }
  return $true
}

# --- collect entries: key = normalized first-60-chars, value = list of "file:line" ---
$entryIndex = @{}
function Add-Entries([string]$path, [string]$label) {
  if (-not (Test-Path -LiteralPath $path)) { return }
  $lines = [IO.File]::ReadAllLines($path, [Text.Encoding]::UTF8)
  $entryStart = -1; $entryLen = 0
  for ($i = 0; $i -lt $lines.Count; $i++) {
    $ln = $lines[$i]
    if ($ln -match '^- \d{4}-\d{2}-\d{2}｜') {
      if ($entryStart -ge 0 -and $entryLen -gt 3) {
        [void]$fail.Add(('{0}: entry at line {1} spans {2} lines (>3)' -f $label, $entryStart, $entryLen))
      }
      $entryStart = $i + 1; $entryLen = 1
      $norm = ($ln -replace '\s+', '' )
      if ($norm.Length -gt 60) { $norm = $norm.Substring(0, 60) }
      $loc = '{0}:L{1}' -f $label, ($i + 1)
      if ($entryIndex.ContainsKey($norm)) { [void]$entryIndex[$norm].Add($loc) }
      else { $entryIndex[$norm] = New-Object System.Collections.Generic.List[string]; [void]$entryIndex[$norm].Add($loc) }
    }
    elseif ($entryStart -ge 0) {
      if ($ln -match '^\S') { # new non-continuation block ends entry
        if ($entryLen -gt 3) { [void]$fail.Add(('{0}: entry at line {1} spans {2} lines (>3)' -f $label, $entryStart, $entryLen)) }
        $entryStart = -1; $entryLen = 0
      } else { $entryLen++ }
    }
  }
  if ($entryStart -ge 0 -and $entryLen -gt 3) {
    [void]$fail.Add(('{0}: entry at line {1} spans {2} lines (>3)' -f $label, $entryStart, $entryLen))
  }
}

# --- malformed date format check ---
function Test-EntryFormat([string]$path, [string]$label) {
  if (-not (Test-Path -LiteralPath $path)) { return }
  $lines = [IO.File]::ReadAllLines($path, [Text.Encoding]::UTF8)
  foreach ($ln in $lines) {
    if ($ln -match '^- \d{4}-\d{2}-\d{2}' -and $ln -notmatch '^- \d{4}-\d{2}-\d{2}｜') {
      [void]$warn.Add(('{0}: entry missing ｜ separator: {1}' -f $label, $ln.Substring(0, [Math]::Min(30, $ln.Length))))
    }
  }
}

# --- hot progress section: keep the active task list short and explicit ---
function Test-HotProgress([string]$text) {
  $lines = $text -split "`r?`n"
  $start = [Array]::IndexOf($lines, '## 进行中（≤3 条；会话结束时更新：目标＋卡点＋下一步）')
  if ($start -lt 0) {
    [void]$warn.Add('MEMORY.md missing 进行中 section')
    return
  }
  $count = 0
  $today = (Get-Date).Date
  for ($i = $start + 1; $i -lt $lines.Count; $i++) {
    if ($lines[$i] -match '^## ') { break }
    if ($lines[$i] -match '^-\s+') {
      $count++
      if ($lines[$i] -notmatch '更新：(?<date>\d{4}-\d{2}-\d{2})') {
        [void]$warn.Add(('MEMORY.md 进行中 entry missing 更新日期: {0}' -f $lines[$i].Substring(0, [Math]::Min(30, $lines[$i].Length))))
      } else {
        try {
          $updated = [DateTime]::ParseExact($Matches['date'], 'yyyy-MM-dd', [Globalization.CultureInfo]::InvariantCulture)
          if (($today - $updated.Date).Days -gt 30) {
            [void]$warn.Add(('MEMORY.md 进行中 entry stale over 30 days: {0}' -f $Matches['date']))
          }
        } catch {
          [void]$warn.Add(('MEMORY.md 进行中 entry has invalid 更新日期: {0}' -f $Matches['date']))
        }
      }
    }
  }
  if ($count -gt 3) { [void]$fail.Add(('MEMORY.md 进行中 entries over cap: {0}' -f $count)) }
}

# --- HOT ---
$hot = Join-Path $root 'MEMORY.md'
if (-not (Test-Path -LiteralPath $hot)) { [void]$fail.Add('missing MEMORY.md') }
else {
  $hotLen = (Get-Item -LiteralPath $hot).Length
  if ($hotLen -gt 10KB) { [void]$fail.Add(('MEMORY.md over cap {0:N1}KB' -f ($hotLen/1KB))) }
  if (-not (Test-Utf8NoBom $hot)) { [void]$fail.Add('MEMORY.md has BOM') }
  $hotText = [IO.File]::ReadAllText($hot, [Text.Encoding]::UTF8)
  if ($hotText -notmatch [regex]::Escape('分类索引')) { [void]$fail.Add('MEMORY.md missing index section') }
  foreach ($s in $spaces) {
    $needle = 'spaces\' + $s + '.md'
    if ($hotText -notmatch [regex]::Escape($needle)) { [void]$fail.Add(('index missing {0}' -f $needle)) }
  }
  Add-Entries $hot 'MEMORY.md'
  Test-EntryFormat $hot 'MEMORY.md'
  Test-HotProgress $hotText
}

# --- RULES.md (static, must exist, must not be modified by automation) ---
$rules = Join-Path $root 'RULES.md'
if (-not (Test-Path -LiteralPath $rules)) { [void]$fail.Add('missing RULES.md') }
elseif ((Get-Item -LiteralPath $rules).Length -gt 4KB) { [void]$fail.Add('RULES.md over 4KB') }

# --- spaces ---
$spaceDir = Join-Path $root 'spaces'
if (-not (Test-Path -LiteralPath $spaceDir)) { [void]$fail.Add('missing spaces dir') }
else {
  foreach ($s in $spaces) {
    $p = Join-Path $spaceDir ($s + '.md')
    if (-not (Test-Path -LiteralPath $p)) { [void]$fail.Add(('missing {0}.md' -f $s)); continue }
    $len = (Get-Item -LiteralPath $p).Length
    if ($len -gt 4KB) { [void]$fail.Add(('{0}.md over cap {1:N1}KB' -f $s, ($len/1KB))) }
    if (-not (Test-Utf8NoBom $p)) { [void]$fail.Add(('{0}.md has BOM' -f $s)) }
    $spaceText = [IO.File]::ReadAllText($p, [Text.Encoding]::UTF8)
    if ($len -lt 200 -and $spaceText -notmatch 'TEMPLATE_PLACEHOLDER') {
      [void]$warn.Add(('{0}.md too empty ({1}B)' -f $s, $len))
    }
    Add-Entries $p $s
    Test-EntryFormat $p $s
  }
}

# --- cross-file duplicate entries ---
foreach ($k in $entryIndex.Keys) {
  $locs = $entryIndex[$k]
  if ($locs.Count -gt 1) {
    $files = $locs | ForEach-Object { ($_ -split ':')[0] } | Sort-Object -Unique
    if ($files.Count -gt 1) {
      [void]$fail.Add(('duplicate entry across files: "{0}..." at {1}' -f $k.Substring(0, [Math]::Min(24, $k.Length)), ($locs -join ',')))
    } elseif ($files.Count -eq 1) {
      [void]$warn.Add(('duplicate entry within file: "{0}..." at {1}' -f $k.Substring(0, [Math]::Min(24, $k.Length)), ($locs -join ',')))
    }
  }
}

$arch = Join-Path $root 'ARCHIVE'
if (-not (Test-Path -LiteralPath $arch)) { [void]$warn.Add('missing ARCHIVE dir') }
else {
  Get-ChildItem -LiteralPath $arch -File | ForEach-Object {
    if ($_.Name -eq '.gitkeep') { return }
    if ($_.Name -notmatch '^\d{4}-\d{2}-.+\.md$') {
      [void]$warn.Add(('ARCHIVE filename not matching YYYY-MM-主题.md: {0}' -f $_.Name))
    }
  }
}

if (Test-Path -LiteralPath $hot) {
  Write-Output ('HOT: {0:N2}KB' -f ((Get-Item -LiteralPath $hot).Length/1KB))
}
if (Test-Path -LiteralPath $spaceDir) {
  Get-ChildItem -LiteralPath $spaceDir -Filter '*.md' | Sort-Object Name | ForEach-Object {
    Write-Output ('  {0}: {1:N2}KB' -f $_.BaseName, ($_.Length/1KB))
  }
}
$entryTotal = 0; foreach ($v in $entryIndex.Values) { $entryTotal += $v.Count }
Write-Output ('ENTRIES: {0} indexed' -f $entryTotal)
if ($warn.Count -gt 0) { Write-Output ('WARN: ' + ($warn -join '; ')) }
if ($fail.Count -gt 0) {
  Write-Output ('FAIL: ' + ($fail -join '; '))
  exit 1
}
Write-Output 'PASS: memory structure OK'
exit 0
