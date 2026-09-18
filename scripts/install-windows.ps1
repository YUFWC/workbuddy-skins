[CmdletBinding()]
param(
  [switch]$NoShortcuts
)

$ErrorActionPreference = 'Stop'

if (-not $env:LOCALAPPDATA) { throw 'LOCALAPPDATA is unavailable' }
$skillRoot = Split-Path -Parent $PSScriptRoot
$runtimeRoot = Join-Path $env:LOCALAPPDATA 'WorkBuddyAmbientSkin'
$engine = Join-Path $runtimeRoot 'engine'
$stage = Join-Path $runtimeRoot ('.engine-stage-' + [guid]::NewGuid().ToString('N'))
$backup = Join-Path $runtimeRoot ('.engine-backup-' + [guid]::NewGuid().ToString('N'))
$oldMoved = $false
$newMoved = $false

function Assert-SafeRuntimePath($Path) {
  $full = [IO.Path]::GetFullPath($Path)
  $local = [IO.Path]::GetFullPath($env:LOCALAPPDATA).TrimEnd('\') + '\'
  if (-not $full.StartsWith($local, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to manage a runtime outside LOCALAPPDATA: $full"
  }
}

function New-AmbientShortcut($Path, $Arguments) {
  $shell = New-Object -ComObject WScript.Shell
  $shortcut = $shell.CreateShortcut($Path)
  $shortcut.TargetPath = (Get-Command powershell.exe -ErrorAction Stop).Source
  $shortcut.Arguments = "-NoLogo -NoProfile -ExecutionPolicy RemoteSigned -File `"$engine\scripts\workbuddy-ambient.ps1`" $Arguments"
  $shortcut.WorkingDirectory = $engine
  $shortcut.Description = 'WorkBuddy Ambient Skin'
  $shortcut.Save()
}

Assert-SafeRuntimePath $runtimeRoot
New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null
New-Item -ItemType Directory -Path $stage -Force | Out-Null

try {
  foreach ($name in @('SKILL.md', 'README.md', 'package.json', 'assets', 'references', 'scripts')) {
    $source = Join-Path $skillRoot $name
    if (Test-Path -LiteralPath $source) { Copy-Item -LiteralPath $source -Destination $stage -Recurse -Force }
  }
  $entry = Join-Path $stage 'scripts\workbuddy-ambient.ps1'
  if (-not (Test-Path -LiteralPath $entry -PathType Leaf)) { throw 'Staged runtime is missing its PowerShell entry point' }
  Get-ChildItem -LiteralPath $stage -Filter '*.ps1' -Recurse | Unblock-File

  if (Test-Path -LiteralPath $engine) {
    Move-Item -LiteralPath $engine -Destination $backup
    $oldMoved = $true
  }
  Move-Item -LiteralPath $stage -Destination $engine
  $newMoved = $true

  if (-not $NoShortcuts) {
    $desktop = [Environment]::GetFolderPath('Desktop')
    $programs = [Environment]::GetFolderPath('Programs')
    New-AmbientShortcut (Join-Path $desktop 'WorkBuddy Ambient Skin.lnk') 'apply --restart confirmed'
    New-AmbientShortcut (Join-Path $programs 'WorkBuddy Ambient Skin.lnk') 'apply --restart confirmed'
    New-AmbientShortcut (Join-Path $programs 'Restore WorkBuddy Appearance.lnk') 'restore --restart confirmed'
  }

  if (Test-Path -LiteralPath $backup) { Remove-Item -LiteralPath $backup -Recurse -Force }
  [ordered]@{ ok = $true; installed = $true; runtime = $engine; shortcuts = -not $NoShortcuts } | ConvertTo-Json -Compress
} catch {
  if ($newMoved -and (Test-Path -LiteralPath $engine)) {
    Remove-Item -LiteralPath $engine -Recurse -Force
  }
  if ($oldMoved -and (Test-Path -LiteralPath $backup)) {
    Move-Item -LiteralPath $backup -Destination $engine
  }
  throw
} finally {
  if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
}
