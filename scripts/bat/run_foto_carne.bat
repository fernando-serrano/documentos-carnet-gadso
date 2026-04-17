@echo off
setlocal
cd /d "%~dp0\..\.."

python run_foto_carne.py
set EXIT_CODE=%ERRORLEVEL%

if not "%EXIT_CODE%"=="0" (
    echo [FOTO CARNE] El flujo termino con error. Codigo=%EXIT_CODE%
)

endlocal & exit /b %EXIT_CODE%
