@echo off
setlocal
cd /d "%~dp0\..\.."

python run_galenius.py
set EXIT_CODE=%ERRORLEVEL%

if not "%EXIT_CODE%"=="0" (
    echo [GALENIUS] El flujo termino con error. Codigo=%EXIT_CODE%
)

endlocal & exit /b %EXIT_CODE%
