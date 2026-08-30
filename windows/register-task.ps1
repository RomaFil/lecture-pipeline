<#
.SYNOPSIS
    Реєструє задачу планувальника, яка будить ПК і запускає sync-lectures.ps1.

.DESCRIPTION
    Три тригери: двічі на день + при вході в систему.

    Тригер «при вході» — не надмірність, а страховка. Wake timer може не спрацювати
    (ноутбук у сумці, вимкнене живлення, налаштування BIOS), і тоді пропущений запуск
    усе одно виконається, щойно ти сядеш за комп`ютер. StartWhenAvailable додає те саме
    з боку самого планувальника.

    ВАЖЛИВО: задача виконується від твого користувача (LogonType Interactive), бо їй
    потрібен доступ до SSH-ключа в профілі. Від SYSTEM ключа не буде.

    Окремо треба УВІМКНУТИ wake timers — за замовчуванням у Windows вони часто вимкнені.
    З-під адміністратора:
        powercfg /SETACVALUEINDEX SCHEME_CURRENT SUB_SLEEP RTCWAKE 1
        powercfg /SETDCVALUEINDEX SCHEME_CURRENT SUB_SLEEP RTCWAKE 1
        powercfg /SETACTIVE SCHEME_CURRENT
    Перевірити, що задача справді реєструє пробудження: powercfg /waketimers
#>
[CmdletBinding()]
param(
    [string]$ScriptPath = (Join-Path $PSScriptRoot 'sync-lectures.ps1'),
    [string]$TaskName   = 'Lectures Sync',
    [string[]]$Times    = @('15:00', '23:00')
)

$ErrorActionPreference = 'Stop'

# Стабільний шлях до pwsh: усередині WindowsApps лежить версійована тека,
# яка змінюється при кожному оновленні PowerShell.
$exe = "$env:LOCALAPPDATA\Microsoft\WindowsApps\pwsh.exe"
if (-not (Test-Path $exe)) { $exe = 'powershell.exe' }

$action = New-ScheduledTaskAction -Execute $exe `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$ScriptPath`" -Mode Both"

$triggers = @()
foreach ($t in $Times) { $triggers += New-ScheduledTaskTrigger -Daily -At $t }
$triggers += New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"

$settings = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -RestartCount 2 -RestartInterval (New-TimeSpan -Minutes 10)

$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers `
    -Settings $settings -Principal $principal -Force `
    -Description 'Конвеєр лекцій: push відео на VPS, pull транскриптів у сховище' | Out-Null

$task = Get-ScheduledTask -TaskName $TaskName
Write-Host "Задачу '$TaskName' зареєстровано."
Write-Host ("  WakeToRun:          {0}" -f $task.Settings.WakeToRun)
Write-Host ("  StartWhenAvailable: {0}" -f $task.Settings.StartWhenAvailable)
Write-Host ("  Наступний запуск:   {0}" -f (Get-ScheduledTaskInfo -TaskName $TaskName).NextRunTime)
Write-Host ""
Write-Host "Перевір, що wake timers увімкнені (з-під адміністратора): powercfg /waketimers"
