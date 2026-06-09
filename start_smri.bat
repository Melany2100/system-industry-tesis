@echo off
setlocal

cd /d "%~dp0"

if exist ".venv\Scripts\activate.bat" (
    call ".venv\Scripts\activate.bat"
) else (
    echo [SMRI] No se encontro .venv\Scripts\activate.bat. Se usara el Python disponible en PATH.
)

set SMRI_AUTOSTART_CAMERAS=1
set SMRI_CAMERA_AUTOSTART_FPS=8

echo [SMRI] Aplicando migraciones pendientes...
python manage.py migrate

if errorlevel 1 (
    echo [SMRI] No se pudo aplicar migraciones. Revisa la base de datos o dependencias.
    pause
    exit /b 1
)

echo [SMRI] Iniciando servidor con autoinicio de camaras...
python manage.py runserver 0.0.0.0:8000

pause
