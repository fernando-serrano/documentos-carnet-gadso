@echo off
setlocal
cd /d "%~dp0\..\.."

echo [FOTO CARNE] Ejecutando run_foto_carne.py
python -u tools\run_foto_carne_force_exit.py
set EXIT_CODE=%ERRORLEVEL%
echo [FOTO CARNE] Proceso Python finalizado. Codigo=%EXIT_CODE%

if not "%EXIT_CODE%"=="0" (
    echo [FOTO CARNE] El flujo termino con error. Codigo=%EXIT_CODE%
)

endlocal & exit /b %EXIT_CODE%
