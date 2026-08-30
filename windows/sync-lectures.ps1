<#
.SYNOPSIS
    Обмін лекційними записами між Windows-ПК і VPS.

.DESCRIPTION
    Push: нові відео з watch-теки → ~/lectures/incoming на VPS, потім тригер обробки.
    Pull: готові транскрипти з ~/lectures/outgoing → у сховище Obsidian.

    Нічого не видаляється, доки наступний крок не підтвердив успіх: після кожної
    передачі звіряються SHA256 з обох боків, і лише при збігу оригінал видаляється.

    Файл заливається під іменем <name>.uploading і перейменовується вже на VPS —
    так процесор ніколи не побачить недокачаний файл.

    Якщо сховище синхронізується плагіном із наскрізним шифруванням (Remotely Save
    тощо), НЕ пиши в його каталог на сервері напряму: там усе зашифроване на боці
    клієнта. Обмін іде окремим каналом через SSH, а клієнт сам підхопить нові
    локальні файли при наступному запуску.

.PARAMETER Mode
    Push, Pull або Both (за замовчуванням).
#>
[CmdletBinding()]
param(
    [ValidateSet('Push', 'Pull', 'Both')]
    [string]$Mode = 'Both'
)

$ErrorActionPreference = 'Stop'

# --- конфіг ------------------------------------------------------------------
$ConfigPath = Join-Path $PSScriptRoot 'config.ps1'
if (-not (Test-Path $ConfigPath)) {
    throw "Немає $ConfigPath. Скопіюй config.example.ps1 у config.ps1 і підстав свої значення."
}
. $ConfigPath

$VideoExt = @('.mkv', '.mp4', '.m4v', '.mov', '.webm', '.flv')
# запис, змінений щойно, може ще писатись OBS — не чіпаємо
$SettleSeconds = 60

# ServerAliveCountMax обов`язковий разом з Interval: ConnectTimeout покриває лише
# встановлення TCP-з`єднання, а не мовчання вже відкритої сесії. Без цієї пари
# запуск за розкладом може зависнути на першому ж виклику і провисіти до
# спрацювання ExecutionTimeLimit. Тепер будь-яке зависання вмирає за ~90 с.
$SshOpts = @('-p', $VpsPort, '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15',
             '-o', 'ServerAliveInterval=30', '-o', 'ServerAliveCountMax=3')
# -p обов`язковий: дата в назві відео береться з mtime файлу. Без нього mtime
# став би часом копіювання, і запис, залитий за logon-тригером наступного ранку,
# отримав би дату наступного дня.
$ScpOpts = @('-P', $VpsPort, '-p', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15',
             '-o', 'ServerAliveInterval=30', '-o', 'ServerAliveCountMax=3', '-q')

# --- логування ---------------------------------------------------------------
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LogFile = Join-Path $LogDir ('sync-{0}.log' -f (Get-Date -Format 'yyyy-MM'))
$script:Pushed = 0; $script:Pulled = 0; $script:Failed = 0

function Write-Log {
    param([string]$Level, [string]$Message)
    $line = '{0} {1,-5} {2}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Level, $Message
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
    Write-Host $line
}

function Invoke-Ssh {
    param([string]$Command)
    $out = & ssh @SshOpts $VpsHost $Command 2>&1
    return [pscustomobject]@{ ExitCode = $LASTEXITCODE; Output = ($out -join "`n").Trim() }
}

function Get-RemoteHash {
    param([string]$RemotePath)
    # одинарні лапки навколо шляху — імена містять пробіли й нелатинські літери
    $r = Invoke-Ssh ("sha256sum '{0}' 2>/dev/null | cut -d' ' -f1" -f $RemotePath)
    if ($r.ExitCode -ne 0) { return $null }
    return $r.Output
}

# --- push --------------------------------------------------------------------
function Invoke-Push {
    if (-not (Test-Path $WatchDir)) {
        Write-Log WARN "watch-тека відсутня: $WatchDir"
        return $false
    }
    $cutoff = (Get-Date).AddSeconds(-$SettleSeconds)
    $files = Get-ChildItem -Path $WatchDir -File |
             Where-Object { $VideoExt -contains $_.Extension.ToLower() -and $_.LastWriteTime -lt $cutoff } |
             Sort-Object LastWriteTime

    if (-not $files) { Write-Log INFO 'push: нових записів немає'; return $false }
    Write-Log INFO ('push: знайдено {0} запис(ів)' -f $files.Count)

    $any = $false
    foreach ($f in $files) {
        try {
            $sizeMb = [math]::Round($f.Length / 1MB, 1)
            Write-Log INFO ('push: {0} ({1} МБ) →' -f $f.Name, $sizeMb)
            $localHash = (Get-FileHash -Path $f.FullName -Algorithm SHA256).Hash.ToLower()

            $tmp   = "$RemoteBase/incoming/$($f.Name).uploading"
            $final = "$RemoteBase/incoming/$($f.Name)"

            & scp @ScpOpts $f.FullName "${VpsHost}:$tmp"
            if ($LASTEXITCODE -ne 0) { throw "scp завершився з кодом $LASTEXITCODE" }

            $remoteHash = Get-RemoteHash $tmp
            if ($remoteHash -ne $localHash) {
                Invoke-Ssh ("rm -f '{0}'" -f $tmp) | Out-Null
                throw "SHA256 не збігається (локально $($localHash.Substring(0,12)), на VPS $remoteHash) — файл на VPS видалено, локальний збережено"
            }

            $mv = Invoke-Ssh ("mv '{0}' '{1}'" -f $tmp, $final)
            if ($mv.ExitCode -ne 0) { throw "не вдалося перейменувати на VPS: $($mv.Output)" }

            # тільки тепер, після підтвердженого збігу чек-сум, прибираємо локальну копію
            Remove-Item -Path $f.FullName -Force
            Write-Log INFO ('push: {0} ПІДТВЕРДЖЕНО (sha {1}), локальну копію видалено' -f $f.Name, $localHash.Substring(0, 12))
            $script:Pushed++
            $any = $true
        }
        catch {
            $script:Failed++
            Write-Log ERROR ('push: {0}: {1}' -f $f.Name, $_.Exception.Message)
        }
    }
    return $any
}

function Start-RemoteProcessing {
    # Відв`язаний запуск: транскрипція півторагодинної пари триває близько години,
    # чекати її в межах scheduled task не можна.
    $cmd = "setsid nohup $RemoteBase/bin/run.sh >> $RemoteBase/logs/run.log 2>&1 < /dev/null & echo started"
    $r = Invoke-Ssh $cmd
    if ($r.ExitCode -eq 0) { Write-Log INFO 'обробку на VPS запущено' }
    else { Write-Log ERROR ('не вдалося запустити обробку: {0}' -f $r.Output); $script:Failed++ }
}

# --- pull --------------------------------------------------------------------
function Invoke-Pull {
    $r = Invoke-Ssh ("find '{0}/outgoing' -type f -name '*.md' -printf '%P\n' 2>/dev/null" -f $RemoteBase)
    if ($r.ExitCode -ne 0) { Write-Log ERROR ('pull: не вдалося прочитати outgoing: {0}' -f $r.Output); $script:Failed++; return }
    $rel = $r.Output -split "`n" | Where-Object { $_ -ne '' }
    if (-not $rel) { Write-Log INFO 'pull: готових транскриптів немає'; return }
    Write-Log INFO ('pull: знайдено {0} транскрипт(ів)' -f $rel.Count)

    foreach ($p in $rel) {
        try {
            $remote = "$RemoteBase/outgoing/$p"
            # <предмет>/<назва>.md
            $localPath = Join-Path $VaultRaw ($p -replace '/', '\')
            New-Item -ItemType Directory -Force -Path (Split-Path $localPath -Parent) | Out-Null

            $remoteHash = Get-RemoteHash $remote
            if (-not $remoteHash) { throw 'не вдалося порахувати sha256 на VPS' }

            & scp @ScpOpts "${VpsHost}:$remote" $localPath
            if ($LASTEXITCODE -ne 0) { throw "scp завершився з кодом $LASTEXITCODE" }

            $localHash = (Get-FileHash -Path $localPath -Algorithm SHA256).Hash.ToLower()
            if ($localHash -ne $remoteHash) {
                Remove-Item -Path $localPath -Force -ErrorAction SilentlyContinue
                throw 'SHA256 не збігається — локальну копію видалено, на VPS залишено'
            }

            # підтверджено — можна прибирати з VPS
            $rm = Invoke-Ssh ("rm -f '{0}'" -f $remote)
            if ($rm.ExitCode -ne 0) { Write-Log WARN ('pull: {0} перенесено, але не видалено з VPS: {1}' -f $p, $rm.Output) }

            Write-Log INFO ('pull: {0} ПІДТВЕРДЖЕНО → {1}' -f $p, $localPath)
            Add-WikiLogEntry -RelPath $p
            $script:Pulled++
        }
        catch {
            $script:Failed++
            Write-Log ERROR ('pull: {0}: {1}' -f $p, $_.Exception.Message)
        }
    }
    # -mindepth 1 обов`язковий: без нього find видаляє і саму теку outgoing,
    # щойно вона спорожніє
    Invoke-Ssh ("find '{0}/outgoing' -mindepth 1 -type d -empty -delete" -f $RemoteBase) | Out-Null
}

function Add-WikiLogEntry {
    param([string]$RelPath)
    if (-not $WikiLog) { return }
    try {
        $subject = ($RelPath -split '/')[0]
        $entry = '{0} | Запис лекції ({1}) | Brain/raw/lectures/{2} | Сирий whisper-транскрипт, автоматичний конвеєр. Змістовна нотатка ще НЕ створена' -f (Get-Date -Format 'yyyy-MM-dd'), $subject, $RelPath
        # Якщо попередній запис не закінчувався переносом рядка, Add-Content
        # приклеїть наш прямо до нього — доводимо файл до ладу перед дописом.
        $raw = [System.IO.File]::ReadAllText($WikiLog)
        if ($raw.Length -gt 0 -and -not $raw.EndsWith("`n")) {
            [System.IO.File]::AppendAllText($WikiLog, "`n", [System.Text.UTF8Encoding]::new($false))
        }
        Add-Content -Path $WikiLog -Value $entry -Encoding UTF8
    }
    catch {
        Write-Log WARN ('не вдалося дописати журнал сховища: {0}' -f $_.Exception.Message)
    }
}

# --- main --------------------------------------------------------------------
Write-Log INFO ('=== запуск ({0}) ===' -f $Mode)
try {
    $ping = Invoke-Ssh 'echo ok'
    if ($ping.ExitCode -ne 0) {
        Write-Log ERROR ('VPS недоступний (VPN піднятий?): {0}' -f $ping.Output)
        exit 1
    }

    $pushedSomething = $false
    if ($Mode -in 'Push', 'Both') { $pushedSomething = Invoke-Push }
    if ($pushedSomething) { Start-RemoteProcessing }
    if ($Mode -in 'Pull', 'Both') { Invoke-Pull }

    $review = Invoke-Ssh ("ls -1 '{0}/_needs-review'/*.* 2>/dev/null | wc -l" -f $RemoteBase)
    if ($review.ExitCode -eq 0 -and [int]$review.Output -gt 0) {
        Write-Log WARN ('УВАГА: {0} файл(ів) у _needs-review на VPS — класифікатор не впізнав дисципліну, потрібен ручний розбір' -f $review.Output)
    }

    Write-Log INFO ('=== підсумок: залито {0}, забрано {1}, помилок {2} ===' -f $script:Pushed, $script:Pulled, $script:Failed)
    exit ($(if ($script:Failed -gt 0) { 1 } else { 0 }))
}
catch {
    Write-Log ERROR ('фатальна помилка: {0}' -f $_.Exception.Message)
    exit 1
}
