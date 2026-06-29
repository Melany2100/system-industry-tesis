@echo off
setlocal

cd /d "%~dp0"

set STARTUP_DIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
set STARTUP_FILE=%STARTUP_DIR%\SMRI_Inicio_Automatico.bat
set AUTOSTART_SCRIPT=%~dp0start_smri_autostart.bat
set HIDDEN_SCRIPT=%~dp0start_smri_hidden.vbs

if not exist "%AUTOSTART_SCRIPT%" (
    echo [SMRI] No se encontro start_smri_autostart.bat en la carpeta del proyecto.
    pause
    exit /b 1
)

if not exist "%HIDDEN_SCRIPT%" (
    echo [SMRI] No se encontro start_smri_hidden.vbs en la carpeta del proyecto.
    pause
    exit /b 1
)

if not exist "%STARTUP_DIR%" (
    echo [SMRI] No se encontro la carpeta Startup de Windows.
    echo Ruta esperada: %STARTUP_DIR%
    pause
    exit /b 1
)

echo @echo off> "%STARTUP_FILE%"
echo cd /d "%~dp0">> "%STARTUP_FILE%"
echo wscript.exe "%HIDDEN_SCRIPT%">> "%STARTUP_FILE%"

echo [SMRI] Inicio automatico registrado correctamente.
echo [SMRI] Archivo creado:
echo %STARTUP_FILE%
echo.
echo El sistema se iniciara cuando este usuario inicie sesion en Windows.
pause
