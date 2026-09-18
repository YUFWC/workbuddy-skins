[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [ValidateSet('inspect', 'is-running', 'quit', 'force-quit', 'launch-cdp', 'launch-normal', 'process-command', 'verify-owner')]
  [string]$Action,
  [int]$Port = 9347,
  [int]$TimeoutMs = 15000,
  [int]$ProcessId = 0
)

$ErrorActionPreference = 'Stop'

function Write-Json($Value) {
  $Value | ConvertTo-Json -Compress -Depth 6
}

function Get-RegistryCandidates {
  $roots = @(
    'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*',
    'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*',
    'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*'
  )
  foreach ($root in $roots) {
    Get-ItemProperty $root -ErrorAction SilentlyContinue |
      Where-Object { $_.DisplayName -like '*WorkBuddy*' } |
      ForEach-Object {
        if ($_.DisplayIcon) { ($_.DisplayIcon -replace ',\d+$', '').Trim('"') }
        if ($_.InstallLocation) { foreach ($n in @('WorkBuddy.exe','WorkBuddyAI.exe')) { Join-Path $_.InstallLocation $n } }
      }
  }
}

function Get-WorkBuddyInfo {
  $candidates = @(
    $env:WORKBUDDY_EXE,
    $(if ($env:LOCALAPPDATA) { Join-Path $env:LOCALAPPDATA 'workbuddy\WorkBuddy.exe' }),
    $(if ($env:LOCALAPPDATA) { Join-Path $env:LOCALAPPDATA 'Programs\workbuddy\WorkBuddy.exe' }),
    $(if ($env:LOCALAPPDATA) { Join-Path $env:LOCALAPPDATA 'Programs\WorkBuddyAI\WorkBuddyAI.exe' }),
    $(if ($env:ProgramFiles) { Join-Path $env:ProgramFiles 'WorkBuddy\WorkBuddy.exe' }),
    $(if (${env:ProgramFiles(x86)}) { Join-Path ${env:ProgramFiles(x86)} 'WorkBuddy\WorkBuddy.exe' })
  ) + @(Get-RegistryCandidates)

  $uniqueCandidates = [Collections.Generic.List[string]]::new()
  $seenCandidates = @{}
  foreach ($candidate in $candidates) {
    if ($null -eq $candidate) { continue }
    $candidateText = [string]$candidate
    if ($candidateText.Length -eq 0) { continue }
    if ($seenCandidates.ContainsKey($candidateText)) { continue }
    $seenCandidates[$candidateText] = $true
    $uniqueCandidates.Add($candidateText)
  }

  foreach ($candidate in $uniqueCandidates) {
    try {
      $path = (Resolve-Path -LiteralPath $candidate -ErrorAction Stop).Path
      $fileName = [IO.Path]::GetFileName($path)
      if ($fileName -cne 'WorkBuddy.exe' -and $fileName -cne 'WorkBuddyAI.exe') { continue }
      $item = Get-Item -LiteralPath $path
      $signature = Get-AuthenticodeSignature -LiteralPath $path
      $productName = $item.VersionInfo.ProductName
      $identityValid = ($signature.Status -eq 'Valid') -or ($productName -like '*WorkBuddy*')
      if (-not $identityValid) { continue }
      return [ordered]@{
        appFound = $true
        appPath = $path
        executable = $path
        identityValid = $true
        bundleMatches = $true
        version = $item.VersionInfo.ProductVersion
        productName = $productName
        signatureStatus = [string]$signature.Status
        platform = 'win32'
      }
    } catch { continue }
  }
  return [ordered]@{ appFound = $false; appPath = $null; executable = $null; identityValid = $false; bundleMatches = $false; platform = 'win32' }
}

function Get-ExactProcesses($Executable, [switch]$MainOnly) {
  $expected = [IO.Path]::GetFullPath($Executable)
  $exeName = [IO.Path]::GetFileName($Executable)
  @(Get-CimInstance Win32_Process -Filter "Name = '$exeName'" | Where-Object {
    if (-not $_.ExecutablePath) { return $false }
    $same = [string]::Equals([IO.Path]::GetFullPath($_.ExecutablePath), $expected, [StringComparison]::OrdinalIgnoreCase)
    if (-not $same) { return $false }
    if ($MainOnly -and $_.CommandLine -match '(?i)(^|\s)--type=') { return $false }
    return $true
  })
}

function Require-WorkBuddy {
  $info = Get-WorkBuddyInfo
  if (-not $info.appFound -or -not $info.identityValid) { throw 'A verified WorkBuddy.exe (or WorkBuddyAI.exe) installation was not found' }
  return $info
}

switch ($Action) {
  'inspect' { Write-Json (Get-WorkBuddyInfo); break }
  'is-running' {
    $info = Get-WorkBuddyInfo
    Write-Json ([bool]($info.appFound -and @(Get-ExactProcesses $info.executable -MainOnly).Count -gt 0))
    break
  }
  'quit' {
    $info = Require-WorkBuddy
    $processes = @(Get-ExactProcesses $info.executable -MainOnly)
    if ($processes.Count -eq 0) { Write-Json @{ wasRunning = $false; stopped = $true }; break }
    foreach ($record in $processes) {
      try { [void](Get-Process -Id $record.ProcessId -ErrorAction Stop).CloseMainWindow() } catch {}
    }
    $deadline = [DateTime]::UtcNow.AddMilliseconds($TimeoutMs)
    do {
      Start-Sleep -Milliseconds 250
      $remaining = @(Get-ExactProcesses $info.executable -MainOnly)
    } while ($remaining.Count -gt 0 -and [DateTime]::UtcNow -lt $deadline)
    if ($remaining.Count -gt 0) { throw 'WorkBuddy did not quit cleanly; no process was force-killed' }
    Write-Json @{ wasRunning = $true; stopped = $true }
    break
  }
  'force-quit' {
    $info = Require-WorkBuddy
    $processes = @(Get-ExactProcesses $info.executable)
    if ($processes.Count -eq 0) { Write-Json @{ wasRunning = $false; stopped = $true; forced = $false; pids = @() }; break }
    $stoppedPids = [Collections.Generic.HashSet[int]]::new()
    $deadline = [DateTime]::UtcNow.AddSeconds(8)
    $emptySince = $null
    while ([DateTime]::UtcNow -lt $deadline) {
      $current = @(Get-ExactProcesses $info.executable)
      if ($current.Count -eq 0) {
        if ($null -eq $emptySince) { $emptySince = [DateTime]::UtcNow }
        if (([DateTime]::UtcNow - $emptySince).TotalMilliseconds -ge 500) { break }
      } else {
        $emptySince = $null
        foreach ($record in $current) {
          $processIdToStop = [int]$record.ProcessId
          $verified = @(Get-ExactProcesses $info.executable | Where-Object { [int]$_.ProcessId -eq $processIdToStop })
          if ($verified.Count -gt 0) {
            Stop-Process -Id $processIdToStop -Force -ErrorAction SilentlyContinue
            [void]$stoppedPids.Add($processIdToStop)
          }
        }
      }
      Start-Sleep -Milliseconds 100
    }
    $remaining = @(Get-ExactProcesses $info.executable)
    if ($remaining.Count -gt 0) { throw 'Verified WorkBuddy process did not stop after forced restart' }
    Start-Sleep -Milliseconds 2000
    if (@(Get-ExactProcesses $info.executable).Count -gt 0) { throw 'WorkBuddy process family restarted before CDP launch' }
    Write-Json @{ wasRunning = $true; stopped = $true; forced = $true; pids = @($stoppedPids); settledMs = 2000 }
    break
  }
  'launch-cdp' {
    if ($Port -lt 1024 -or $Port -gt 65535) { throw 'Invalid CDP port' }
    $info = Require-WorkBuddy
    $arguments = @('--remote-debugging-address=127.0.0.1', "--remote-debugging-port=$Port")
    $process = Start-Process -FilePath $info.executable -ArgumentList $arguments -PassThru
    Write-Json @{ pid = $process.Id; port = $Port; executable = $info.executable }
    break
  }
  'launch-normal' {
    $info = Require-WorkBuddy
    [void](Start-Process -FilePath $info.executable -PassThru)
    Write-Json @{ launched = $true; executable = $info.executable }
    break
  }
  'process-command' {
    if ($ProcessId -lt 1) { Write-Json ''; break }
    $record = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
    Write-Json $(if ($record) { [string]$record.CommandLine } else { '' })
    break
  }
  'verify-owner' {
    if ($Port -lt 1024 -or $Port -gt 65535) { Write-Json $false; break }
    $info = Require-WorkBuddy
    $listeners = @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
      Where-Object { $_.LocalAddress -in @('127.0.0.1', '::1') })
    foreach ($listener in $listeners) {
      $pidToCheck = [int]$listener.OwningProcess
      $seen = @{}
      for ($depth = 0; $depth -lt 8 -and $pidToCheck -gt 0 -and -not $seen.ContainsKey($pidToCheck); $depth += 1) {
        $seen[$pidToCheck] = $true
        $record = Get-CimInstance Win32_Process -Filter "ProcessId = $pidToCheck" -ErrorAction SilentlyContinue
        if (-not $record) { break }
        if ($record.ExecutablePath -and [string]::Equals([IO.Path]::GetFullPath($record.ExecutablePath), [IO.Path]::GetFullPath($info.executable), [StringComparison]::OrdinalIgnoreCase)) {
          Write-Json $true
          exit 0
        }
        $pidToCheck = [int]$record.ParentProcessId
      }
    }
    Write-Json $false
    break
  }
}
