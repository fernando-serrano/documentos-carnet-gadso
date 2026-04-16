@echo off
setlocal

cd /d "%~dp0"

if not exist "logs" mkdir "logs"
if not exist "data" mkdir "data"
if not exist "test" mkdir "test"
if not exist "__pycache__" mkdir "__pycache__"

for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set TS=%%i
set RUN_DIR=logs\runs\scheduled_%TS%
if not exist "%RUN_DIR%" mkdir "%RUN_DIR%"
set RUN_LOG=%RUN_DIR%\run_scheduled_%TS%.log

set RUN_MODE=scheduled
set SCHEDULED_MULTIWORKER=1
if "%SCHEDULED_WORKERS%"=="" set SCHEDULED_WORKERS=4
set CARNET_COMPARE_ALLOW_EMPTY_ESTADO=0
set CARNET_LOG_RUNS_KEEP_DIRS=10
set LOG_DIR=%RUN_DIR%

echo [INFO] Modo scheduled multihilo activado
echo [INFO] Carpeta de corrida: %RUN_DIR%
echo [INFO] RUN_MODE=%RUN_MODE% SCHEDULED_MULTIWORKER=%SCHEDULED_MULTIWORKER% SCHEDULED_WORKERS=%SCHEDULED_WORKERS%
echo [INFO] Guardando consola en %RUN_LOG%

python carnet_emision.py >> "%RUN_LOG%" 2>&1
set EXIT_CODE=%ERRORLEVEL%

if not "%EXIT_CODE%"=="0" (
  echo.
  echo [ERROR] El flujo termino con codigo %EXIT_CODE%.
  echo [ERROR] Revisa: %RUN_LOG%
) else (
  echo.
  echo [OK] Flujo finalizado correctamente.
  echo [OK] Log de consola: %RUN_LOG%
)

pause
exit /b %EXIT_CODE%
