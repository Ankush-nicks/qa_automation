@echo off
REM Daily QA export on Windows (runs on its own via Task Scheduler - see README.md).
REM Writes the CSVs into the project's "exports" folder:
REM   exports\latest\*.csv        same names every day (upload these to the app)
REM   exports\YYYYMMDD\*.csv      one folder per day (history)
REM   exports\daily_log.txt       what happened on each run
setlocal
cd /d "%~dp0"
set QA_EXPORT_DIR=%~dp0..\exports
if not exist "%QA_EXPORT_DIR%" mkdir "%QA_EXPORT_DIR%"
echo ===== %date% %time% ===== >> "%QA_EXPORT_DIR%\daily_log.txt"
python run_exports.py >> "%QA_EXPORT_DIR%\daily_log.txt" 2>&1
if errorlevel 1 (echo FAILED - see log >> "%QA_EXPORT_DIR%\daily_log.txt")
endlocal
