<#
.SYNOPSIS
    Registers the "Lectures Sync" scheduled task: wakes the PC and runs run-sync.cmd.

.DESCRIPTION
    This mirrors the task that actually runs on the author's PC (checked against
    an exported XML on 06.11.2026). Every setting below is deliberate:

      Action     cmd.exe /c run-sync.cmd Both
                 The wrapper writes a START line before pwsh starts and an EXIT
                 line after it ends (incident 003), so "never started" and
                 "started and froze" can be told apart in the log.
      Triggers   1. at logon
                 2. 1 minute after the session is unlocked
                 3. every 2 hours from 08:00 for 13 hours (08:00 .. 20:00)
                 4. daily at 22:00
                 The logon/unlock triggers are insurance: a wake timer may not
                 fire (laptop in a bag, power settings), and then the missed run
                 happens as soon as you sit down. StartWhenAvailable adds the
                 same from the scheduler's side.
      Limits     ExecutionTimeLimit 30 min, MultipleInstances IgnoreNew,
                 WakeToRun, 2 restarts every 10 min, runs on battery.

    The task runs as the current user (LogonType Interactive) because it needs the
    SSH key from the user profile; SYSTEM would not have it.

    SAFE BY DEFAULT. If a task with this name already exists the script stops.
    With -Overwrite it first exports the existing task to
    LecturesSync-backup-<yyyyMMdd-HHmmss>.xml next to this script and only then
    replaces it. These backups contain your user name and SID: they are
    git-ignored, never commit them.

    Wake timers must be enabled separately (default is often off). From an
    elevated prompt:
        powercfg /SETACVALUEINDEX SCHEME_CURRENT SUB_SLEEP RTCWAKE 1
        powercfg /SETDCVALUEINDEX SCHEME_CURRENT SUB_SLEEP RTCWAKE 1
        powercfg /SETACTIVE SCHEME_CURRENT
    Check that the task really arms a wake: powercfg /waketimers

.PARAMETER Wrapper
    Full path of run-sync.cmd. Defaults to the one next to this script. It derives
    its own folder from its location, so sync-lectures.ps1 must sit beside it.

.PARAMETER Overwrite
    Replace an existing task (a backup XML is exported first).

.PARAMETER Disabled
    Register the task but leave it disabled. Used to test the registration
    without any trigger being able to fire.
#>
[CmdletBinding()]
param(
    [string]$Wrapper    = '',
    [string]$TaskName   = 'Lectures Sync',
    [string]$Mode       = 'Both',
    [string]$DayStart   = '08:00',
    [string]$EveningAt  = '22:00',
    [switch]$Overwrite,
    [switch]$Disabled
)

$ErrorActionPreference = 'Stop'

# $PSScriptRoot is empty inside param() defaults on Windows PowerShell 5.1, so the
# default for -Wrapper is resolved here, in the body, where it is reliable.
$here = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
if (-not $Wrapper) { $Wrapper = Join-Path $here 'run-sync.cmd' }

if (-not (Test-Path -LiteralPath $Wrapper)) {
    throw "run-sync.cmd not found: $Wrapper"
}
$userId = "$env:USERDOMAIN\$env:USERNAME"

# --- never replace silently --------------------------------------------------
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    if (-not $Overwrite) {
        throw "Task '$TaskName' already exists. Re-run with -Overwrite (a backup XML is exported first)."
    }
    $backup = Join-Path $here ('LecturesSync-backup-{0}.xml' -f (Get-Date -Format 'yyyyMMdd-HHmmss'))
    Export-ScheduledTask -TaskName $TaskName | Set-Content -Path $backup -Encoding Unicode
    Write-Host "Existing task backed up to: $backup"
}

# --- action: cmd.exe /c ""<wrapper>" <mode>" ----------------------------------
# The inner quotes keep a path with spaces working; cmd strips the outer pair.
$cmd    = Join-Path $env:SystemRoot 'System32\cmd.exe'
$action = New-ScheduledTaskAction -Execute $cmd -Argument ('/c ""{0}" {1}"' -f $Wrapper, $Mode)

# --- triggers ------------------------------------------------------------------
$logon = New-ScheduledTaskTrigger -AtLogOn -User $userId

# Session unlock (TASK_SESSION_UNLOCK = 8) has no New-ScheduledTaskTrigger switch,
# so build it from the CIM class.
$stateClass = Get-CimClass -Namespace 'Root/Microsoft/Windows/TaskScheduler' `
                           -ClassName 'MSFT_TaskSessionStateChangeTrigger'
$unlock = New-CimInstance -CimClass $stateClass -ClientOnly -Property @{
    StateChange = 8
    Delay       = 'PT1M'
    UserId      = $userId
    Enabled     = $true
}

# A daily trigger that repeats: build a repeating one-shot and borrow its
# Repetition block (New-ScheduledTaskTrigger cannot combine -Daily with repetition).
$repeat = New-ScheduledTaskTrigger -Once -At $DayStart `
    -RepetitionInterval (New-TimeSpan -Hours 2) -RepetitionDuration (New-TimeSpan -Hours 13)
$daytime = New-ScheduledTaskTrigger -Daily -At $DayStart
$daytime.Repetition = $repeat.Repetition
# The cmdlet defaults StopAtDurationEnd to $true, which would kill a sync that is
# still running when the 13-hour window closes. The live task lets it finish.
$daytime.Repetition.StopAtDurationEnd = $false

$evening = New-ScheduledTaskTrigger -Daily -At $EveningAt

# --- settings and principal -----------------------------------------------------
$settings = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
    -RestartCount 2 -RestartInterval (New-TimeSpan -Minutes 10)
if ($Disabled) { $settings.Enabled = $false }

$principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action `
    -Trigger @($logon, $unlock, $daytime, $evening) `
    -Settings $settings -Principal $principal -Force `
    -Description 'Lecture pipeline: push videos to the VPS, pull transcripts into the notes vault' | Out-Null

$task = Get-ScheduledTask -TaskName $TaskName
$info = Get-ScheduledTaskInfo -TaskName $TaskName
Write-Host "Task '$TaskName' registered (state: $($task.State))."
Write-Host ("  Triggers:           {0}" -f $task.Triggers.Count)
Write-Host ("  WakeToRun:          {0}" -f $task.Settings.WakeToRun)
Write-Host ("  StartWhenAvailable: {0}" -f $task.Settings.StartWhenAvailable)
Write-Host ("  ExecutionTimeLimit: {0}" -f $task.Settings.ExecutionTimeLimit)
Write-Host ("  Next run:           {0}" -f $info.NextRunTime)
Write-Host ""
Write-Host "Check that wake timers are enabled (elevated prompt): powercfg /waketimers"
