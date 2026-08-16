@echo off
setlocal

set PORT=8000
set FOUND_PROCESS=0

echo [SMRI] Buscando servidor en el puerto %PORT%...

for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":%PORT%" ^| findstr "LISTENING"') do (
    set FOUND_PROCESS=1
    echo [SMRI] Deteniendo proceso PID %%P...
    taskkill /PID %%P /F
)

if "%FOUND_PROCESS%"=="0" (
    echo [SMRI] No se encontro ningun servidor escuchando en el puerto %PORT%.
) else (
    echo [SMRI] Servidor detenido.
)

pause
