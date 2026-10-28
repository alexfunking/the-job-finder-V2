@echo off
rem Daily run for Task Scheduler: scan the boards, then import the last 3 days of LinkedIn alerts.
rem Output is appended to data\logs\daily.log. Exit code is the scan's (0 ok, 1 a board failed, 2 config
rem error), or the alerts import's if the scan was clean.
setlocal
cd /d "%~dp0.."
if not exist data\logs mkdir data\logs
set "LOG=data\logs\daily.log"
set "PYTHONIOENCODING=utf-8"

echo ==== %date% %time% ==== >> "%LOG%"
uv run jobfinder scan >> "%LOG%" 2>&1
set "SCAN_EXIT=%errorlevel%"
uv run jobfinder alerts --days 3 >> "%LOG%" 2>&1
set "ALERTS_EXIT=%errorlevel%"
echo exit: scan %SCAN_EXIT%, alerts %ALERTS_EXIT% >> "%LOG%"

if not "%SCAN_EXIT%"=="0" exit /b %SCAN_EXIT%
exit /b %ALERTS_EXIT%
