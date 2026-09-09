<#
.SYNOPSIS
    Тести Windows-боку конвеєра. Запуск: pwsh -File windows\test-sync.ps1

.DESCRIPTION
    НАВІЩО. Три з дев'яти інцидентів конвеєра сталися саме тут, і жоден не мав
    тесту:
      005 — число під українською локаллю їхало як "36,4", і bash на тому боці
            не міг порівняти його з порогом. Перевірка диска мовчала місяць.
      006 — апостроф у назві ("Комп'ютерні мережі") закривав одинарну лапку
            достроково, sha256sum падав, транскрипт зациклився в outgoing.
      009 — квадратні дужки з часом ("[14-54]") для -Path є wildcard-класом
            символів; Get-FileHash повертав $null і верифікація pull падала.

    Спільне в усіх трьох: ламався не обмін даними, а РОЗБІР РЯДКА шаром, який
    його передає. Тому тести тут саме про це — і працюють на справжніх файлах
    і справжньому bash, а не на здогадках про поведінку.

    Скрипт дот-сорсить sync-lectures.ps1 з -LoadOnly (визначає функції й не
    виконує синк) і підсовує йому тимчасовий config.ps1 — на VPS не ходить,
    мережі не торкається.
#>
[CmdletBinding()]
param([string]$ScriptPath)

$ErrorActionPreference = 'Stop'
$script:Passed = 0
$script:Failed = 0

function Test-Case {
    param([string]$Name, [scriptblock]$Body)
    try {
        & $Body
        Write-Host ('  OK   {0}' -f $Name)
        $script:Passed++
    }
    catch {
        Write-Host ('  FAIL {0}: {1}' -f $Name, $_.Exception.Message)
        $script:Failed++
    }
}

function Assert-Equal {
    param($Expected, $Actual, [string]$What = 'значення')
    if ($Expected -ne $Actual) {
        throw ('{0}: очікував <{1}>, отримав <{2}>' -f $What, $Expected, $Actual)
    }
}

function Assert-True {
    param([bool]$Condition, [string]$What)
    if (-not $Condition) { throw $What }
}

# --- пісочниця з тимчасовим config.ps1 ---------------------------------------
if (-not $ScriptPath) { $ScriptPath = Join-Path $PSScriptRoot 'sync-lectures.ps1' }
if (-not (Test-Path -LiteralPath $ScriptPath)) { throw "Немає $ScriptPath" }

$Sandbox = Join-Path ([System.IO.Path]::GetTempPath()) ('sync-test-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
$null = New-Item -ItemType Directory -Force -Path $Sandbox
$Vault = Join-Path $Sandbox 'vault'
$Watch = Join-Path $Sandbox 'watch'
$Logs = Join-Path $Sandbox 'logs'
foreach ($d in @($Vault, $Watch, $Logs)) { $null = New-Item -ItemType Directory -Force -Path $d }
$WikiLogPath = Join-Path $Vault 'wiki_log.md'

$TestScript = Join-Path $Sandbox 'sync-lectures.ps1'
Copy-Item -LiteralPath $ScriptPath -Destination $TestScript

$cfg = @(
    ('$VpsHost    = ''nobody@127.0.0.1''')
    ('$VpsPort    = ''65000''')
    ('$RemoteBase = ''/tmp/lectures''')
    ('$WatchDir   = ''{0}''' -f $Watch)
    ('$VaultRaw   = ''{0}''' -f $Vault)
    ('$WikiLog    = ''{0}''' -f $WikiLogPath)
    ('$LogDir     = ''{0}''' -f $Logs)
) -join "`n"
Set-Content -LiteralPath (Join-Path $Sandbox 'config.ps1') -Value $cfg -Encoding UTF8

. $TestScript -LoadOnly

# ЗАПОБІЖНИК. Тест дописує в журнал і возиться з файлами, тож мусить бути
# доведено, що всі шляхи ведуть у пісочницю. Якщо перевіряти скрипт із
# зашитим конфігом (а не з config.ps1), змінні лишаться бойовими — і тест
# напише в СПРАВЖНІЙ wiki_log сховища. Це не гіпотеза: рівно так і сталося
# 09.09.2026 на першому прогоні проти живої копії, довелось чистити журнал.
foreach ($pair in @(@{n = 'WikiLog'; v = $WikiLog }, @{n = 'VaultRaw'; v = $VaultRaw },
        @{n = 'WatchDir'; v = $WatchDir }, @{n = 'LogDir'; v = $LogDir })) {
    if (-not $pair.v -or -not $pair.v.StartsWith($Sandbox)) {
        Remove-Item -LiteralPath $Sandbox -Recurse -Force -ErrorAction SilentlyContinue
        throw ("`$$($pair.n) вказує ПОЗА пісочницю (<$($pair.v)>). Скрипт не бере конфіг " +
            "із config.ps1 — тест зупинено, щоб не зіпсувати робочі дані.")
    }
}

Write-Host ''
Write-Host ('пісочниця: {0}' -f $Sandbox)
Write-Host ''

# Імена, на яких конвеєр уже ламався або зламається наступним. Перші два —
# реальні назви транскриптів з Brain/raw/lectures, решта — той самий клас
# символів. Усі рядки в одинарних лапках, щоб PowerShell не з'їв нічого сам.
$HostileNames = @(
    'Математичний аналіз. Частина 3 — дослідження збіжності рядів (практика, 08.09.2026) [14-54].md'
    'Комп''ютерні мережі та безпека за технологіями CISCO — тлумачення понять (04.09.2026).md'
    'Загальна фізика [10-25] — тема з `бектіком і $знаком.md'
    'Бокс — робота на лапах (тренування, 09.09.2026) [14-15].md'
    'Ajax Embedded Linux Internship — FHS і дерево каталогів (17-02).md'
)

# Знайти РОБОЧИЙ bash, а не перший-ліпший у PATH. На Windows `bash.exe` — це
# зазвичай заглушка WSL, і якщо дистрибутива немає, вона мовчки повертає
# порожній рядок у stdout, а помилку пише в stderr. Тест тоді "падає" на
# правильному коді. Тому кандидати перевіряються поведінкою: хто справді
# віддав "ok", той і bash.
function Resolve-Bash {
    $candidates = @()
    foreach ($p in @("$env:ProgramFiles\Git\bin\bash.exe",
            "$env:ProgramFiles\Git\usr\bin\bash.exe",
            "${env:ProgramFiles(x86)}\Git\bin\bash.exe",
            "$env:LOCALAPPDATA\Programs\Git\bin\bash.exe")) {
        if ($p -and (Test-Path -LiteralPath $p)) { $candidates += $p }
    }
    $git = Get-Command git -ErrorAction SilentlyContinue
    if ($git) {
        $root = Split-Path (Split-Path $git.Source -Parent) -Parent
        foreach ($rel in @('bin\bash.exe', 'usr\bin\bash.exe')) {
            $p = Join-Path $root $rel
            if (Test-Path -LiteralPath $p) { $candidates += $p }
        }
    }
    foreach ($c in (Get-Command bash -All -ErrorAction SilentlyContinue)) { $candidates += $c.Source }

    foreach ($c in ($candidates | Select-Object -Unique)) {
        try {
            $out = & $c -c 'printf ok' 2>$null
            if ($out -eq 'ok') { return $c }
        }
        catch { }
    }
    return $null
}
$bash = Resolve-Bash
if ($bash) { Write-Host ('bash: {0}' -f $bash) } else { Write-Host 'bash: НЕ ЗНАЙДЕНО' }

Write-Host '=== ConvertTo-ShQuoted: рядок має пережити shell цілим (інцидент 006) ==='
foreach ($n in $HostileNames) {
    $short = $n.Substring(0, [Math]::Min(44, $n.Length))
    Test-Case ('shell-лапки: ' + $short) {
        if (-not $bash) { throw 'bash не знайдено — тест не можна вважати пройденим' }
        $quoted = ConvertTo-ShQuoted $n
        # Справжня перевірка: віддати рядок справжньому shell і забрати назад.
        # Порівнювати з очікуваним виглядом лапок марно — питання не в тому, як
        # рядок виглядає, а чи виживає.
        $got = & $bash -c "printf '%s' $quoted"
        Assert-Equal $n $got 'рядок після shell'
    }
}
Test-Case 'shell-лапки: спроба ін''єкції не виконується' {
    if (-not $bash) { throw 'bash не знайдено' }
    # Перевірка за ПОБІЧНИМ ЕФЕКТОМ, а не за текстом. Шукати в результаті слово
    # "ЗЛАМАНО" безглуздо: воно є в самому рядку ін'єкції, тож перевірка була б
    # тавтологічно хибною (і саме так вона й провалилась на першому прогоні).
    # Єдиний чесний доказ — що вставлена команда НЕ створила файл.
    $marker = Join-Path $Sandbox 'pwned.flag'
    $shMarker = ($marker -replace '\\', '/')
    $evil = 'лекція''; touch "' + $shMarker + '"; echo '''
    $quoted = ConvertTo-ShQuoted $evil
    $got = & $bash -c "printf '%s' $quoted"
    Assert-Equal $evil $got 'рядок повернувся цілим, не розібраним на команди'
    Assert-True (-not (Test-Path -LiteralPath $marker)) 'вставлена команда не виконалась'
}

Write-Host ''
Write-Host '=== -LiteralPath: дужки в імені не є wildcard (інцидент 009) ==='
foreach ($n in $HostileNames) {
    $short = $n.Substring(0, [Math]::Min(44, $n.Length))
    Test-Case ('хеш і видалення: ' + $short) {
        $f = Join-Path $Sandbox $n
        Set-Content -LiteralPath $f -Value 'вміст' -Encoding UTF8 -NoNewline
        $h = (Get-FileHash -LiteralPath $f -Algorithm SHA256).Hash
        Assert-True ($null -ne $h -and $h.Length -eq 64) 'Get-FileHash -LiteralPath дав хеш'
        Remove-Item -LiteralPath $f -Force
        Assert-True (-not (Test-Path -LiteralPath $f)) 'файл видалено'
    }
}
Test-Case 'регресія 009: саме -Path і ламався (дужки як клас символів)' {
    $f = Join-Path $Sandbox 'регресія [14-54].md'
    Set-Content -LiteralPath $f -Value 'x' -Encoding UTF8 -NoNewline
    $viaPath = $null
    try { $viaPath = Get-FileHash -Path $f -Algorithm SHA256 -ErrorAction SilentlyContinue } catch { }
    Remove-Item -LiteralPath $f -Force
    # Якщо цей тест колись почне падати — PowerShell змінив поведінку -Path.
    # Тоді коментарі про інцидент 009 треба перечитати, а не мовчки прибрати.
    Assert-True ($null -eq $viaPath) '-Path не знаходить файл із дужками (саме це й був баг)'
}
Test-Case 'тека з дужками створюється (у New-Item немає -LiteralPath)' {
    $d = Join-Path $Sandbox 'предмет [дужки]'
    [void][System.IO.Directory]::CreateDirectory($d)
    Assert-True (Test-Path -LiteralPath $d) 'теку створено'
}

Write-Host ''
Write-Host '=== лінт джерела: -Path з іменем файлу більше не має зустрічатись ==='
Test-Case 'жодного -Path на змінній, що приходить з імені файлу' {
    $src = @(Get-Content -LiteralPath $ScriptPath)
    $bad = @()
    for ($i = 0; $i -lt $src.Count; $i++) {
        # (?<=\s) обов'язковий: без нього регулярка ловить хвіст Split-Path
        # і Join-Path, і лінт падає на цілком правильному рядку.
        if ($src[$i] -match '(?<=\s)-Path\s+\$(f\.FullName|localPath|localFile|dest|target|archived)') {
            $bad += ('рядок {0}: {1}' -f ($i + 1), $src[$i].Trim())
        }
    }
    if ($bad.Count -gt 0) { throw ($bad -join ' | ') }
}

Write-Host ''
Write-Host '=== Format-Invariant: крапка, а не кома (інцидент 005) ==='
foreach ($c in @('uk-UA', 'de-DE', 'en-US', 'ru-RU')) {
    Test-Case ('локаль ' + $c) {
        $old = [System.Threading.Thread]::CurrentThread.CurrentCulture
        try {
            [System.Threading.Thread]::CurrentThread.CurrentCulture = [System.Globalization.CultureInfo]::new($c)
            Assert-Equal '36.4' (Format-Invariant 36.4) 'дробове'
            Assert-Equal '40' (Format-Invariant 40.0) 'ціле без хвоста'
            Assert-Equal '-1' (Format-Invariant -1) 'ознака "не вдалося виміряти"'
            Assert-True ((Format-Invariant 36.4) -notmatch ',') 'коми немає'
        }
        finally { [System.Threading.Thread]::CurrentThread.CurrentCulture = $old }
    }
}

Write-Host ''
Write-Host '=== Test-FileReady: файл, який ще пишуть, не йде на push ==='
Test-Case 'зайнятий -> false, вільний -> true' {
    $f = Join-Path $Watch '2026-09-09 14-15-00.mkv'
    Set-Content -LiteralPath $f -Value 'дані' -Encoding UTF8 -NoNewline
    $fi = Get-Item -LiteralPath $f
    Assert-Equal $true (Test-FileReady $fi) 'вільний файл готовий'
    $held = [System.IO.File]::Open($f, [System.IO.FileMode]::Open,
        [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
    try { Assert-Equal $false (Test-FileReady $fi) 'зайнятий файл не готовий' }
    finally { $held.Dispose() }
    Assert-Equal $true (Test-FileReady $fi) 'після звільнення знову готовий'
    Remove-Item -LiteralPath $f -Force
}

Write-Host ''
Write-Host '=== Add-WikiLogEntry: рядок дописується, а не приклеюється ==='
Test-Case 'до файлу без переносу в кінці дописується з нового рядка' {
    Set-Content -LiteralPath $WikiLogPath -Value 'попередній рядок без переносу' -Encoding UTF8 -NoNewline
    Add-WikiLogEntry -RelPath 'Бокс/Бокс — робота на лапах (09.09.2026) [14-15].md' -LocalPath 'C:\tmp\x.md'
    $lines = @(Get-Content -LiteralPath $WikiLogPath)
    Assert-Equal 2 $lines.Count 'рядків стало два, а не один склеєний'
    Assert-True ($lines[1] -like '*Бокс*') 'дисципліну взято з першого сегмента шляху'
    Assert-True ($lines[1] -like '*vault-ingest*') 'позначка, що змістовної нотатки ще немає'
}
Test-Case 'дужки з часом у назві не ламають запис у журнал' {
    Set-Content -LiteralPath $WikiLogPath -Value '' -Encoding UTF8
    Add-WikiLogEntry -RelPath 'Математичний аналіз/Мат. аналіз — ряди (практика, 08.09.2026) [14-54].md' -LocalPath 'C:\tmp\y.md'
    $txt = Get-Content -LiteralPath $WikiLogPath -Raw
    Assert-True ($txt -like '*`[14-54`]*') 'час у назві вцілів'
}

Remove-Item -LiteralPath $Sandbox -Recurse -Force -ErrorAction SilentlyContinue

Write-Host ''
Write-Host ('ПІДСУМОК: пройдено {0}, провалено {1}' -f $script:Passed, $script:Failed)
exit ($(if ($script:Failed -gt 0) { 1 } else { 0 }))
