@echo off
setlocal
cd /d "%~dp0"

if "%~1"=="" goto run_all
if /I "%~1"=="all" goto run_all
if /I "%~1"=="galenius" goto run_galenius
if /I "%~1"=="foto_carne" goto run_foto_carne
if /I "%~1"=="dj_fut" goto run_dj_fut
if /I "%~1"=="firma_digital" goto run_firma_digital

echo Uso: run.bat [all^|galenius^|foto_carne^|dj_fut^|firma_digital]
exit /b 2

:prepare_shared_lote
if defined GLOBAL_LOTE_DIR exit /b 0
set "RUN_TS="
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format 'dd-MM-yyyy-HH-mm-ss'"') do set "RUN_TS=%%i"
echo(%RUN_TS%| findstr /r "^[0-9][0-9]-[0-9][0-9]-[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]-[0-9][0-9]$" >nul || set "RUN_TS="
if not defined RUN_TS (
  echo [ERROR] No se pudo generar identificador de lote.
  exit /b 1
)
set "GLOBAL_LOTE_DIR=%CD%\lotes\lote-%RUN_TS%"
if not exist "%GLOBAL_LOTE_DIR%" mkdir "%GLOBAL_LOTE_DIR%"
echo [RUN] Lote compartido: %GLOBAL_LOTE_DIR%
exit /b 0

:run_galenius
call :prepare_shared_lote
if errorlevel 1 exit /b 1
call "scripts\bat\run_galenius_login.bat"
exit /b %ERRORLEVEL%

:run_foto_carne
call :prepare_shared_lote
if errorlevel 1 exit /b 1
call "scripts\bat\run_foto_carne.bat"
exit /b %ERRORLEVEL%

:run_dj_fut
call :prepare_shared_lote
if errorlevel 1 exit /b 1
call "scripts\bat\run_dj_fut.bat"
exit /b %ERRORLEVEL%

:run_firma_digital
call :prepare_shared_lote
if errorlevel 1 exit /b 1
call "scripts\bat\run_firma_digital.bat"
exit /b %ERRORLEVEL%

:run_all
call :prepare_shared_lote
if errorlevel 1 exit /b 1

set "FLOW_NAME=galenius"
echo [RUN] Iniciando GALENIUS...
call "scripts\bat\run_galenius_login.bat"
set "EXIT_CODE=%ERRORLEVEL%"
echo [RUN] GALENIUS finalizo con codigo %EXIT_CODE%.
if not "%EXIT_CODE%"=="0" goto flow_error

set "FLOW_NAME=foto_carne"
echo [RUN] Iniciando FOTO CARNE...
call "scripts\bat\run_foto_carne.bat"
set "EXIT_CODE=%ERRORLEVEL%"
echo [RUN] FOTO CARNE finalizo con codigo %EXIT_CODE%.
if not "%EXIT_CODE%"=="0" goto flow_error

set "FLOW_NAME=dj_fut"
echo [RUN] Iniciando DJ FUT...
call "scripts\bat\run_dj_fut.bat"
set "EXIT_CODE=%ERRORLEVEL%"
echo [RUN] DJ FUT finalizo con codigo %EXIT_CODE%.
if not "%EXIT_CODE%"=="0" goto flow_error

set "FLOW_NAME=firma_digital"
echo [RUN] Iniciando FIRMA DIGITAL...
call "scripts\bat\run_firma_digital.bat"
set "EXIT_CODE=%ERRORLEVEL%"
echo [RUN] FIRMA DIGITAL finalizo con codigo %EXIT_CODE%.
if not "%EXIT_CODE%"=="0" goto flow_error

echo [RUN] Flujo completo finalizado en lote compartido.
exit /b 0

:flow_error
echo [ERROR] El flujo %FLOW_NAME% termino con codigo %EXIT_CODE%.
exit /b %EXIT_CODE%
