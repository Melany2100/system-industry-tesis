@echo off
setlocal

cd /d "%~dp0"

if not exist "logs" (
    mkdir "logs"
)

set LOG_FILE=%~dp0logs\smri_autostart.log

echo ============================================================ >> "%LOG_FILE%"
echo [SMRI] Inicio automatico: %date% %time% >> "%LOG_FILE%"
echo [SMRI] Carpeta del proyecto: %cd% >> "%LOG_FILE%"

echo [SMRI] Esperando servicios del sistema... >> "%LOG_FILE%"
ping 127.0.0.1 -n 21 > nul

if exist ".venv\Scripts\activate.bat" (
    call ".venv\Scripts\activate.bat"
    echo [SMRI] Entorno virtual activado. >> "%LOG_FILE%"
) else (
    echo [SMRI] No se encontro .venv\Scripts\activate.bat. Se usara Python del PATH. >> "%LOG_FILE%"
)

set SMRI_AUTOSTART_CAMERAS=1
set SMRI_CAMERA_AUTOSTART_FPS=8

echo [SMRI] Verificando Django... >> "%LOG_FILE%"
python manage.py check >> "%LOG_FILE%" 2>&1

if errorlevel 1 (
    echo [SMRI] Error en manage.py check. No se inicia el servidor. >> "%LOG_FILE%"
    exit /b 1
)

echo [SMRI] Aplicando migraciones pendientes... >> "%LOG_FILE%"
python manage.py migrate >> "%LOG_FILE%" 2>&1

if errorlevel 1 (
    echo [SMRI] No se pudo aplicar migraciones. Revisa PostgreSQL, .env o dependencias. >> "%LOG_FILE%"
    exit /b 1
)

echo [SMRI] Iniciando servidor en http://127.0.0.1:8000/login/ >> "%LOG_FILE%"
echo [SMRI] Las camaras activas se iniciaran automaticamente. >> "%LOG_FILE%"

python manage.py runserver 0.0.0.0:8000 --noreload >> "%LOG_FILE%" 2>&1

echo [SMRI] Servidor detenido: %date% %time% >> "%LOG_FILE%"
echo [SMRI] Codigo de salida del servidor: %errorlevel% >> "%LOG_FILE%"
exit /b 0
