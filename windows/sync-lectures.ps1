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
    [string]$Mode = 'Both',

    # Визначити функції й зупинитись, не виконуючи синк. Потрібне тестам:
    # інакше `. .\sync-lectures.ps1` полізе на VPS замість того, щоб просто
    # віддати функції для перевірки. Три з дев'яти інцидентів конвеєра —
    # 005 (кома в числі), 006 (апостроф у лапках), 009 (дужки як wildcard) —
    # жили саме тут, у розборі рядків, і жоден не мав тесту.
    [switch]$LoadOnly
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

# Скільки чекати на ОДИН ssh-виклик і скільки разів пробувати.
#
# ServerAliveCountMax ріже мовчазну сесію, але не рятує від живого сервера:
# sshd бадьоро відповідає на keepalive, а команда не повертається.
#
# Причина (семплер VPS, 04.09.2026): RSS whisper`а осцилює 2.7 <-> 4.8 ГБ
# з періодом близько хвилини, і в піку на машині з 5.9 ГБ лишається ~450 МБ
# вільної пам`яті. Якщо fork нової ssh-сесії потрапляє в цей пік — процес іде
# в непереривний сон (procs_blocked=1 у знімку рівно у вікні зависання).
#
# Звідси й стратегія: не одна довга спроба, а кілька коротких. Вікно біди
# триває ~30 с, тож три спроби по 45 с майже напевно дають одній із них
# потрапити в западину. Гірший випадок 3*45+2*10 = 155 с, і це все одно
# вп`ятеро менше за ExecutionTimeLimit задачі.
$SshTimeoutSec  = 45
$SshRetries     = 3
$SshRetryPause  = 10

# ServerAliveCountMax обов`язковий разом з Interval: ConnectTimeout покриває лише
# встановлення TCP-з`єднання, а не мовчання вже відкритої сесії. Без цієї пари
# 30.08.2026 запуск о 15:00 завис на першому ж виклику і провисів дві години,
# доки його не вбив ExecutionTimeLimit. Тепер будь-яке зависання вмирає за ~90 с.
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

function Invoke-SshOnce {
    param([string]$Command)

    # Запускаємо через Start-Process, а не `& ssh`, лише заради одного: тримати
    # об`єкт процесу, щоб мати кого вбити по таймауту. Побічний бонус — після
    # вбивства не лишається осиротілий ssh.exe, як 04.09 після таймліміту задачі.
    $outFile = [System.IO.Path]::GetTempFileName()
    $errFile = [System.IO.Path]::GetTempFileName()
    try {
        $sshArgs = @($SshOpts) + @($VpsHost, $Command)
        $p = Start-Process -FilePath 'ssh' -ArgumentList $sshArgs -NoNewWindow -PassThru `
                           -RedirectStandardOutput $outFile -RedirectStandardError $errFile

        if (-not $p.WaitForExit($SshTimeoutSec * 1000)) {
            try { $p.Kill($true) } catch { try { $p.Kill() } catch { } }
            $p.WaitForExit(5000) | Out-Null
            # 124 — той самий код, яким про таймаут повідомляє GNU timeout
            return [pscustomobject]@{ ExitCode = 124; Output = "ssh-виклик убитий по таймауту ${SshTimeoutSec} с" }
        }

        $stdout = Get-Content $outFile -Raw -ErrorAction SilentlyContinue
        $stderr = Get-Content $errFile -Raw -ErrorAction SilentlyContinue
        # stderr підмішуємо тільки при помилці: на успіху виклики на кшталт
        # sha256sum чи `wc -l` розбираються як чисте значення, і зайвий рядок
        # попередження від ssh зламав би і звірку хешів, і [int]-приведення.
        $text = if ($p.ExitCode -eq 0) { $stdout }
                else { (@($stdout, $stderr) | Where-Object { $_ }) -join "`n" }

        return [pscustomobject]@{ ExitCode = $p.ExitCode; Output = ("$text").Trim() }
    }
    finally {
        Remove-Item $outFile, $errFile -Force -ErrorAction SilentlyContinue
    }
}

function Invoke-Ssh {
    param([string]$Command)

    # Повторюємо ТІЛЬКИ таймаут (код 124). Змістовна помилка від сервера —
    # це відповідь, а не збій зв`язку: її повторення нічого не змінить,
    # а от повторити `rm` чи `mv` наосліп — спосіб зробити гірше.
    for ($i = 1; $i -le $SshRetries; $i++) {
        $r = Invoke-SshOnce $Command
        if ($r.ExitCode -ne 124) {
            if ($i -gt 1) { Write-Log INFO ('ssh: вдалося з {0}-ї спроби' -f $i) }
            return $r
        }
        if ($i -lt $SshRetries) {
            Write-Log WARN ('ssh: спроба {0}/{1} перевищила {2} с — чекаю {3} с і пробую знову' -f `
                            $i, $SshRetries, $SshTimeoutSec, $SshRetryPause)
            Start-Sleep -Seconds $SshRetryPause
        }
    }
    Write-Log ERROR ('ssh: усі {0} спроби перевищили {1} с — {2}' -f $SshRetries, $SshTimeoutSec, $Command)
    return $r
}

function Test-FileReady {
    param([System.IO.FileInfo]$File)
    # $SettleSeconds дивиться на LastWriteTime, а цього мало: для .mkv OBS
    # оновлює mtime рідко, тому пара, яка пишеться просто зараз, виглядає
    # "усталеною" і йде на push. 03.09 і 04.09 це дало три фальшиві ERROR
    # і, гірше, ставило задачі LastTaskResult=1 на цілком здоровому запуску.
    # Єдина надійна перевірка — спробувати відкрити файл на ексклюзивне читання.
    try {
        $fs = [System.IO.File]::Open($File.FullName, [System.IO.FileMode]::Open,
                                     [System.IO.FileAccess]::Read, [System.IO.FileShare]::None)
        $fs.Dispose()
        return $true
    }
    catch [System.IO.IOException] {
        return $false
    }
    # інші винятки (немає прав, файл зник) навмисно летять далі — це вже помилка,
    # а не "ще пишеться", і про неї має бути ERROR у лозі
}

function ConvertTo-ShQuoted {
    param([string]$Text)
    # Класичний POSIX-ідіом: закрити лапку, вставити екранований апостроф,
    # відкрити знову. Обгортання в одинарні лапки покривало пробіли й кирилицю,
    # але не АПОСТРОФ — а він закриває лапку достроково і ламає всю команду.
    # 04.09.2026 на назві "Комп`ютерні мережі..." pull зациклився: sha256sum
    # падав щоразу, транскрипт лежав в outgoing, кожен запуск давав ту саму
    # помилку. Українські назви повні апострофів, тож це не рідкісний випадок.
    return "'" + ($Text -replace "'", "'\''") + "'"
}

function Format-Invariant {
    param($Value)
    # Числа ЗАВЖДИ через InvariantCulture: українська локаль дає "36,4",
    # а bash на тому боці такого не порівняє — перевірка тихо не спрацює
    # (інцидент 005: сторож місяць не міг зрівняти диск із порогом).
    # Винесено з нутрощів Send-PcStatus 09.09.2026 рівно щоб це стало
    # перевірюваним — вкладена функція тестові недоступна.
    return ([double]$Value).ToString('0.#', [System.Globalization.CultureInfo]::InvariantCulture)
}

function Get-RemoteHash {
    param([string]$RemotePath)
    # одинарні лапки навколо шляху — імена містять пробіли й кирилицю
    $r = Invoke-Ssh ("sha256sum {0} 2>/dev/null | cut -d' ' -f1" -f (ConvertTo-ShQuoted $RemotePath))
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
            if (-not (Test-FileReady $f)) {
                # Не помилка: OBS ще пише цю пару. Забере наступний запуск.
                Write-Log INFO ('push: {0} ще записується (файл зайнятий) — пропускаю' -f $f.Name)
                continue
            }

            $sizeMb = [math]::Round($f.Length / 1MB, 1)
            Write-Log INFO ('push: {0} ({1} МБ) →' -f $f.Name, $sizeMb)
            $localHash = (Get-FileHash -LiteralPath $f.FullName -Algorithm SHA256).Hash.ToLower()

            $tmp   = "$RemoteBase/incoming/$($f.Name).uploading"
            $final = "$RemoteBase/incoming/$($f.Name)"

            & scp @ScpOpts $f.FullName "${VpsHost}:$tmp"
            if ($LASTEXITCODE -ne 0) { throw "scp завершився з кодом $LASTEXITCODE" }

            $remoteHash = Get-RemoteHash $tmp
            if ($remoteHash -ne $localHash) {
                Invoke-Ssh ("rm -f {0}" -f (ConvertTo-ShQuoted $tmp)) | Out-Null
                throw "SHA256 не збігається (локально $($localHash.Substring(0,12)), на VPS $remoteHash) — файл на VPS видалено, локальний збережено"
            }

            $mv = Invoke-Ssh ("mv {0} {1}" -f (ConvertTo-ShQuoted $tmp), (ConvertTo-ShQuoted $final))
            if ($mv.ExitCode -ne 0) { throw "не вдалося перейменувати на VPS: $($mv.Output)" }

            # тільки тепер, після підтвердженого збігу чек-сум, прибираємо локальну копію
            Remove-Item -LiteralPath $f.FullName -Force
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
    # Відв`язаний запуск: транскрипція 1.5-годинної пари триває годинами,
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
            # <slug>/<title>.md
            $localPath = Join-Path $VaultRaw ($p -replace '/', '\')
            [void][System.IO.Directory]::CreateDirectory((Split-Path $localPath -Parent))

            $remoteHash = Get-RemoteHash $remote
            if (-not $remoteHash) { throw 'не вдалося порахувати sha256 на VPS' }

            & scp @ScpOpts "${VpsHost}:$remote" $localPath
            if ($LASTEXITCODE -ne 0) { throw "scp завершився з кодом $LASTEXITCODE" }

            # -LiteralPath, а не -Path, і це не косметика. Ім'я транскрипту з 07.09.2026
            # несе час запису у квадратних дужках — `[14-54]`. Для -Path це не текст,
            # а wildcard-клас символів PowerShell: збігів нема, Get-FileHash повертає
            # $null, і `.Hash` падає з «You cannot call a method on a null-valued
            # expression». Файл при цьому вже успішно скачаний і цілий — ламається
            # рівно верифікація, тобто транскрипт не видаляється з VPS, не потрапляє
            # в wiki_log, а сторож щошість годин звинувачує ПК у тому, що той спав.
            # Так було з практикою матаналізу 08.09: три прогони поспіль, один і той
            # самий ERROR. Скрізь, де шлях приходить з імені файлу, — тільки
            # -LiteralPath (у New-Item такого параметра немає взагалі, тому там
            # .NET-виклик).
            $localHash = (Get-FileHash -LiteralPath $localPath -Algorithm SHA256).Hash.ToLower()
            if ($localHash -ne $remoteHash) {
                Remove-Item -LiteralPath $localPath -Force -ErrorAction SilentlyContinue
                throw 'SHA256 не збігається — локальну копію видалено, на VPS залишено'
            }

            # підтверджено — можна прибирати з VPS
            $rm = Invoke-Ssh ("rm -f {0}" -f (ConvertTo-ShQuoted $remote))
            if ($rm.ExitCode -ne 0) { Write-Log WARN ('pull: {0} перенесено, але не видалено з VPS: {1}' -f $p, $rm.Output) }

            Write-Log INFO ('pull: {0} ПІДТВЕРДЖЕНО → {1}' -f $p, $localPath)
            Add-WikiLogEntry -RelPath $p -LocalPath $localPath
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
    param([string]$RelPath, [string]$LocalPath)
    # Формат той самий, що й у решти wiki_log: дата | джерело | призначення | нотатка
    try {
        $subject = ($RelPath -split '/')[0]
        $name = [System.IO.Path]::GetFileNameWithoutExtension($RelPath)
        $entry = '{0} | Запис лекції ({1}) | Brain/raw/lectures/{2} | Сирий whisper-транскрипт, автоматичний конвеєр (VPS: whisper large-v3 + локальна LLM-класифікація, відео на YouTube unlisted). Змістовна нотатка ще НЕ створена — потрібен `vault-ingest`' -f (Get-Date -Format 'yyyy-MM-dd'), $subject, $RelPath
        # Якщо попередній запис не закінчувався переносом рядка, Add-Content
        # приклеїть наш прямо до нього — доводимо файл до ладу перед дописом.
        $raw = [System.IO.File]::ReadAllText($WikiLog)
        if ($raw.Length -gt 0 -and -not $raw.EndsWith("`n")) {
            [System.IO.File]::AppendAllText($WikiLog, "`n", [System.Text.UTF8Encoding]::new($false))
        }
        Add-Content -Path $WikiLog -Value $entry -Encoding UTF8
        Write-Log INFO ('wiki_log: додано запис для "{0}"' -f $name)
    }
    catch {
        Write-Log WARN ('не вдалося дописати wiki_log: {0}' -f $_.Exception.Message)
    }
}

# --- звіт про стан ПК ---------------------------------------------------------
function Send-PcStatus {
    # ПК звітує ФАКТАМИ, судить VPS. Причина: сторож має жити там, де завжди
    # ввімкнено. ПК на Modern Standby сам є частиною проблеми — коли він не
    # прокидається, він не може й поскаржитись. Тому вся логіка алертів лишається
    # в одному місці (alert.sh), а сюди не треба ані токена, ані порогів.
    #
    # Передаємо base64: у значеннях трапляються пробіли, кирилиця й дужки,
    # і будь-яке інше екранування через ssh -> bash рано чи пізно зламається.
    try {
        $now = Get-Date
        $kv  = [ordered]@{}

        $kv['ts']       = $now.ToString('yyyy-MM-dd HH:mm:ss')
        $kv['ts_epoch'] = [int][double]::Parse((Get-Date -UFormat %s))
        $kv['host']     = $env:COMPUTERNAME

        function fmt($v) { return Format-Invariant $v }

        # --- диск під записи ---
        try {
            $d = Get-PSDrive -Name C -ErrorAction Stop
            $kv['disk_free_gb']  = fmt ([math]::Round($d.Free / 1GB, 1))
            $kv['disk_total_gb'] = fmt ([math]::Round(($d.Free + $d.Used) / 1GB, 1))
        } catch { $kv['disk_free_gb'] = -1; $kv['disk_total_gb'] = -1 }

        # --- що лежить у watch-теці незалитим ---
        try {
            $pend = @(Get-ChildItem -Path $WatchDir -File -ErrorAction Stop |
                      Where-Object { $VideoExt -contains $_.Extension.ToLower() })
            $kv['pending_count'] = $pend.Count
            if ($pend.Count -gt 0) {
                $oldest = ($pend | Sort-Object LastWriteTime | Select-Object -First 1)
                $kv['pending_oldest_h'] = fmt ([math]::Round(($now - $oldest.LastWriteTime).TotalHours, 1))
                $kv['pending_names']    = (($pend | Select-Object -First 5).Name -join ' | ')
            } else {
                $kv['pending_oldest_h'] = 0
                $kv['pending_names']    = ''
            }
            # OBS іменує файли як "РРРР-ММ-ДД ГГ-ХХ-СС.mkv" — дата прямо в назві
            $kv['recordings_today'] = @($pend | Where-Object { $_.Name -like ($now.ToString('yyyy-MM-dd') + '*') }).Count
        } catch {
            $kv['pending_count'] = -1; $kv['pending_oldest_h'] = -1
            $kv['pending_names'] = ''; $kv['recordings_today'] = -1
        }

        # --- стан задачі планувальника ---
        # LastTaskResult свідомо НЕ шлемо: звіт збирається всередині самого
        # запуску, тому там завжди 267009 (SCHED_S_TASK_RUNNING) — поле, яке
        # ніколи не змінюється, гірше за відсутнє. Замість нього — скільки
        # запусків за добу завершились ненульовим кодом (подія 201).
        try {
            $ti = Get-ScheduledTask -TaskName 'Lectures Sync' -ErrorAction Stop | Get-ScheduledTaskInfo
            $kv['task_last_run'] = $ti.LastRunTime.ToString('yyyy-MM-dd HH:mm:ss')
        } catch { $kv['task_last_run'] = '' }

        try {
            $fails = @(Get-WinEvent -FilterHashtable @{
                           LogName   = 'Microsoft-Windows-TaskScheduler/Operational'
                           Id        = 201
                           StartTime = $now.AddHours(-24)
                       } -ErrorAction SilentlyContinue |
                       Where-Object { $_.Message -match 'Lectures Sync' -and $_.Message -notmatch 'кодом повернення 0\.' })
            $kv['task_failures_24h'] = $fails.Count
        } catch { $kv['task_failures_24h'] = -1 }

        # --- скільки разів за добу задачу вбило за таймлімітом (подія 329) ---
        try {
            $to = @(Get-WinEvent -FilterHashtable @{
                        LogName   = 'Microsoft-Windows-TaskScheduler/Operational'
                        Id        = 329
                        StartTime = $now.AddHours(-24)
                    } -ErrorAction SilentlyContinue |
                    Where-Object { $_.Message -match 'Lectures Sync' })
            $kv['task_timeouts_24h'] = $to.Count
        } catch { $kv['task_timeouts_24h'] = -1 }

        # --- чи спрацював нічний запуск о 23:00 (гіпотеза 4b) ---
        try {
            $nightStart = $now.Date.AddDays(-1).AddHours(22).AddMinutes(55)
            if ($now.Hour -lt 2) { $nightStart = $nightStart.AddDays(-1) }
            $nw = @(Get-WinEvent -FilterHashtable @{
                        LogName   = 'Microsoft-Windows-TaskScheduler/Operational'
                        Id        = @(100, 110)
                        StartTime = $nightStart
                        EndTime   = $nightStart.AddMinutes(20)
                    } -ErrorAction SilentlyContinue |
                    Where-Object { $_.Message -match 'Lectures Sync' })
            $kv['night_wake_ok'] = if ($nw.Count -gt 0) { 1 } else { 0 }
        } catch { $kv['night_wake_ok'] = -1 }

        # --- живлення: ноут на 3 % батареї не прокинеться жодним таймером ---
        try {
            $b = Get-CimInstance Win32_Battery -ErrorAction Stop | Select-Object -First 1
            if ($b) {
                $kv['on_ac']       = if ($b.BatteryStatus -eq 2) { 1 } else { 0 }
                $kv['battery_pct'] = $b.EstimatedChargeRemaining
            } else { $kv['on_ac'] = 1; $kv['battery_pct'] = 100 }
        } catch { $kv['on_ac'] = -1; $kv['battery_pct'] = -1 }

        $kv['sync_pushed'] = $script:Pushed
        $kv['sync_pulled'] = $script:Pulled
        $kv['sync_failed'] = $script:Failed

        $text = ($kv.GetEnumerator() | ForEach-Object { '{0}={1}' -f $_.Key, $_.Value }) -join "`n"
        $b64  = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($text))

        $r = Invoke-Ssh ("mkdir -p '{0}/state' && echo '{1}' | base64 -d > '{0}/state/pc-status.env'" -f $RemoteBase, $b64)
        if ($r.ExitCode -ne 0) { Write-Log WARN ('статус ПК не доставлено: {0}' -f $r.Output) }
    }
    catch {
        # звіт про стан ніколи не має валити сам синк
        Write-Log WARN ('не вдалося зібрати статус ПК: {0}' -f $_.Exception.Message)
    }
}

# --- main --------------------------------------------------------------------
# Тест дот-сорсить цей файл, щоб дістатись функцій. Без цієї межі він одразу
# поліз би на VPS.
if ($LoadOnly) { return }

Write-Log INFO ('=== запуск ({0}) ===' -f $Mode)
try {
    $ping = Invoke-Ssh 'echo ok'
    if ($ping.ExitCode -ne 0) {
        Write-Log ERROR ('VPS недоступний (WireGuard піднятий?): {0}' -f $ping.Output)
        exit 1
    }

    $pushedSomething = $false
    if ($Mode -in 'Push', 'Both') { $pushedSomething = Invoke-Push }
    if ($pushedSomething) { Start-RemoteProcessing }
    if ($Mode -in 'Pull', 'Both') { Invoke-Pull }

    $review = Invoke-Ssh ("ls -1 '{0}/_needs-review'/*.mkv '{0}/_needs-review'/*.mp4 2>/dev/null | wc -l" -f $RemoteBase)
    if ($review.ExitCode -eq 0 -and [int]$review.Output -gt 0) {
        Write-Log WARN ('УВАГА: {0} запис(ів) у _needs-review на VPS — класифікатор не впізнав дисципліну, потрібен ручний розбір' -f $review.Output)
    }

    Write-Log INFO ('=== підсумок: залито {0}, забрано {1}, помилок {2} ===' -f $script:Pushed, $script:Pulled, $script:Failed)
    Send-PcStatus
    exit ($(if ($script:Failed -gt 0) { 1 } else { 0 }))
}
catch {
    Write-Log ERROR ('фатальна помилка: {0}' -f $_.Exception.Message)
    # Звітуємо навіть після падіння: сторожу на VPS важливо знати, що ПК живий
    # і що саме в нього не так. Мовчання читалося б як «ПК зник».
    Send-PcStatus
    exit 1
}
