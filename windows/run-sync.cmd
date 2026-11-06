@echo off
rem ============================================================================
rem  run-sync.cmd - Task Scheduler wrapper around sync-lectures.ps1
rem
rem  WHY: incident 003, night of 03-04.09.2026. The scheduler launched the task,
rem  it was killed on ExecutionTimeLimit, and the log had NOT A SINGLE LINE,
rem  because the first Write-Log call lives inside the PowerShell script. From
rem  the outside "never started" and "started and froze" looked identical.
rem
rem  This wrapper logs one line BEFORE pwsh starts and one AFTER it exits:
rem    START without EXIT  -> process was created but did not survive
rem    no START at all     -> the scheduler never launched anything
rem  cmd.exe is in System32 and is not an MSIX package, so START is written even
rem  if it is activation of the Store pwsh alias that stalls.
rem
rem  Pure ASCII on purpose: cmd writes bytes in the ANSI codepage, the log is
rem  UTF-8. For ASCII those are the same bytes, for Cyrillic they are not.
rem  Must stay CRLF: with LF-only endings cmd desyncs and executes comment text
rem  (.gitattributes pins *.cmd to CRLF).
rem
rem  Install: put this file next to sync-lectures.ps1 and point the scheduled
rem  task at it:  cmd.exe /c "<this folder>\run-sync.cmd" Both
rem  Paths below are derived from the folder this file lives in.
rem ============================================================================
setlocal

set "MODE=%~1"
if "%MODE%"=="" set "MODE=Both"

set "HERE=%~dp0"
set "LOGDIR=%HERE%logs"
set "SCRIPT=%HERE%sync-lectures.ps1"
set "PWSH=%LOCALAPPDATA%\Microsoft\WindowsApps\pwsh.exe"

if not exist "%LOGDIR%" mkdir "%LOGDIR%"

call :stamp
>>"%LOGFILE%" echo %STAMP% START task launched by scheduler, mode=%MODE%

"%PWSH%" -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "%SCRIPT%" -Mode %MODE%
set "RC=%ERRORLEVEL%"

call :stamp
>>"%LOGFILE%" echo %STAMP% EXIT  pwsh finished, exit code=%RC%

endlocal & exit /b %RC%

rem --- timestamp + current month log file, recomputed on every call so a run
rem     crossing midnight into a new month lands in the right file.
rem     Locale assumed dd.MM.yyyy / HH:mm:ss,ff.
:stamp
set "_D=%DATE%"
set "_T=%TIME: =0%"
set "STAMP=%_D:~6,4%-%_D:~3,2%-%_D:~0,2% %_T:~0,8%"
set "LOGFILE=%LOGDIR%\sync-%_D:~6,4%-%_D:~3,2%.log"
goto :eof
