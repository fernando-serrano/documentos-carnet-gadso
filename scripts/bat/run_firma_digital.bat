@echo off
setlocal
cd /d "%~dp0\..\.."
python run_firma_digital.py
set EXIT_CODE=%ERRORLEVEL%
if not "%EXIT_CODE%"=="0" (
  echo.
  echo [ERROR] run_firma_digital.py termino con codigo %EXIT_CODE%.
)
endlocal & exit /b %EXIT_CODE%
