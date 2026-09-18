[CmdletBinding(PositionalBinding = $false)]
param(
  [string]$WorkBuddyExe,
  [Parameter(ValueFromRemainingArguments = $true)]
  [string[]]$AmbientArguments
)

$ErrorActionPreference = 'Stop'

# 用 .NET Process API 探测 Node 版本。
# 不用 `& node -p '...'`：PS 5.1 对含双引号的参数与 native stderr 的处理有差异，
# 会让探测静默失败（被 try/catch 吞掉后 continue），最终误报"未找到 Node"。
function Get-NodeMajor([string]$NodeExe) {
  $proc = $null
  try {
    $procInfo = New-Object System.Diagnostics.ProcessStartInfo
    $procInfo.FileName = $NodeExe
    $procInfo.Arguments = '--no-warnings --version'
    $procInfo.UseShellExecute = $false
    $procInfo.RedirectStandardOutput = $true
    $procInfo.RedirectStandardError = $true
    $procInfo.CreateNoWindow = $true
    $proc = [System.Diagnostics.Process]::Start($procInfo)
    $stdout = $proc.StandardOutput.ReadToEnd()
    [void]$proc.StandardError.ReadToEnd()
    [void]$proc.WaitForExit(15000)
    $match = [regex]::Match([string]$stdout, '\d+')
    if (-not $match.Success) { return 0 }
    return ([int]$match.Groups[0].Value)
  } catch {
    return 0
  } finally {
    # ProcessStartInfo 不实现 IDisposable，不能对它调用 Dispose；
    # 而且 finally 里抛异常会覆盖掉函数返回值，所以这里必须整体容错。
    if ($null -ne $proc) { try { $proc.Dispose() } catch {} }
  }
}

# 探测不可用时（终端限制 native exec 等），从 .../node/versions/<版本>/node.exe 目录名取主版本
function Get-NodeMajorFromInstallDir([string]$NodeExe) {
  try {
    $parentDir = [IO.Path]::GetDirectoryName($NodeExe)
    if (-not ($parentDir -like '*\node\versions*')) { return 0 }
    $dirName = [IO.Path]::GetFileName($parentDir)
    $match = [regex]::Match($dirName, '^(?:v)?(\d+)')
    if (-not $match.Success) { return 0 }
    return ([int]$match.Groups[1].Value)
  } catch {
    return 0
  }
}

function Find-CompatibleNode {
  $allCandidates = @()
  if ($env:WORKBUDDY_NODE) { $allCandidates += $env:WORKBUDDY_NODE }
  $command = Get-Command node.exe -ErrorAction SilentlyContinue
  if ($command) { $allCandidates += $command.Source }
  $command = Get-Command node -ErrorAction SilentlyContinue
  if ($command) { $allCandidates += $command.Source }
  foreach ($versionsRoot in @(
      (Join-Path $env:USERPROFILE '.workbuddy\binaries\node\versions'),
      (Join-Path $env:USERPROFILE '.workbuddy-ai\binaries\node\versions'))) {
    if (Test-Path -LiteralPath $versionsRoot) {
      foreach ($item in @(Get-ChildItem -LiteralPath $versionsRoot -Directory |
        Sort-Object { try { [version]$_.Name } catch { [version]'0.0' } } -Descending)) {
        $allCandidates += Join-Path $item.FullName 'node.exe'
      }
    }
  }

  $seenNode = @{}
  foreach ($candidate in $allCandidates) {
    if ($null -eq $candidate) { continue }
    $candidateText = [string]$candidate
    if ($candidateText.Length -eq 0) { continue }
    if ($seenNode.ContainsKey($candidateText)) { continue }
    $seenNode[$candidateText] = $true
    if (-not (Test-Path -LiteralPath $candidateText -PathType Leaf)) { continue }

    $major = Get-NodeMajor $candidateText
    if ($major -lt 22) { $major = Get-NodeMajorFromInstallDir $candidateText }
    if ($major -ge 22) { return (Resolve-Path -LiteralPath $candidateText).Path }
  }
  throw 'Node.js 22 or newer was not found. Install Node.js 22+ or set WORKBUDDY_NODE.'
}

$AllowedExeNames = @('WorkBuddy.exe', 'WorkBuddyAI.exe')
function Test-AllowedExeName([string]$Name) {
  return [array]::IndexOf($AllowedExeNames, $Name.Trim()) -ge 0
}
if ($WorkBuddyExe) {
  $resolved = (Resolve-Path -LiteralPath $WorkBuddyExe -ErrorAction Stop).Path
  if (-not (Test-AllowedExeName ([IO.Path]::GetFileName($resolved)))) { throw '-WorkBuddyExe must point to WorkBuddy.exe or WorkBuddyAI.exe' }
  $env:WORKBUDDY_EXE = $resolved
}

$entry = Join-Path $PSScriptRoot 'ambient.mjs'
if (-not (Test-Path -LiteralPath $entry -PathType Leaf)) { throw "Missing entry point: $entry" }
$node = Find-CompatibleNode
$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$mutex = [Threading.Mutex]::new($false, "Local\WorkBuddyAmbientSkin.$sid.Operation")
$locked = $false
try {
  $locked = $mutex.WaitOne(0)
  if (-not $locked) { throw 'Another WorkBuddy Ambient Skin operation is already running' }
  & $node $entry @AmbientArguments
  exit $LASTEXITCODE
} finally {
  if ($locked) { try { $mutex.ReleaseMutex() } catch {} }
  $mutex.Dispose()
}
