@echo off
rem Daily run for Task Scheduler: scan the boards, import the last 3 days of LinkedIn alerts, have the local
rem model read the new postings, then send what is new to Telegram. Saved answers cost nothing, and a job that
rem failed is retried tomorrow; so is a digest that failed to send.
rem Output is appended to data\logs\daily.log, except the extraction's job-by-job list, which is long:
rem data\logs\extract.log keeps only the latest run. Exit code is the first non-zero of scan (0 ok, 1 a board
rem failed, 2 config error), alerts, extract (1 a job failed, for example Ollama was not running) and digest.
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
uv run jobfinder extract --all --extractor ollama > data\logs\extract.log 2>&1
set "EXTRACT_EXIT=%errorlevel%"
uv run jobfinder digest >> "%LOG%" 2>&1
set "DIGEST_EXIT=%errorlevel%"
echo exit: scan %SCAN_EXIT%, alerts %ALERTS_EXIT%, extract %EXTRACT_EXIT%, digest %DIGEST_EXIT% >> "%LOG%"

if not "%SCAN_EXIT%"=="0" exit /b %SCAN_EXIT%
if not "%ALERTS_EXIT%"=="0" exit /b %ALERTS_EXIT%
if not "%EXTRACT_EXIT%"=="0" exit /b %EXTRACT_EXIT%
exit /b %DIGEST_EXIT%
