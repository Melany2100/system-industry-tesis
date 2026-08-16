$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$LogDir = Join-Path $ProjectDir "logs"
$LogFile = Join-Path $LogDir "smri_autostart.log"
$OutLog = Join-Path $LogDir "smri_server_stdout.log"
$ErrLog = Join-Path $LogDir "smri_server_stderr.log"
$VenvPython = Join-Path $ProjectDir ".venv\Scripts\python.exe"

if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
}

function Write-SmriLog {
    param([string]$Message)
    Add-Content -Path $LogFile -Value $Message
}

Write-SmriLog "============================================================"
Write-SmriLog "[SMRI] Inicio automatico PowerShell: $(Get-Date)"
Write-SmriLog "[SMRI] Carpeta del proyecto: $ProjectDir"
Write-SmriLog "[SMRI] Esperando servicios del sistema..."
Start-Sleep -Seconds 20

if (Test-Path $VenvPython) {
    $Python = $VenvPython
    Write-SmriLog "[SMRI] Python virtual: $Python"
} else {
    $Python = "python"
    Write-SmriLog "[SMRI] No se encontro .venv. Se usara Python del PATH."
}


$env:SMRI_AUTOSTART_CAMERAS = "1"
$env:SMRI_CAMERA_AUTOSTART_FPS = "2"

Set-Location $ProjectDir

Write-SmriLog "[SMRI] Verificando Django..."
& $Python manage.py check *>> $LogFile
if ($LASTEXITCODE -ne 0) {
    Write-SmriLog "[SMRI] Error en manage.py check. No se inicia el servidor."
    exit 1
}

Write-SmriLog "[SMRI] Aplicando migraciones pendientes..."
& $Python manage.py migrate *>> $LogFile
if ($LASTEXITCODE -ne 0) {
    Write-SmriLog "[SMRI] No se pudo aplicar migraciones. Revisa PostgreSQL, .env o dependencias."
    exit 1
}

$Existing = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
if ($Existing) {
    Write-SmriLog "[SMRI] Ya existe un proceso escuchando en el puerto 8000. No se inicia otro servidor."
    exit 0
}

Write-SmriLog "[SMRI] Iniciando servidor oculto en http://127.0.0.1:8000/login/"
Write-SmriLog "[SMRI] Las camaras activas se iniciaran automaticamente."

$Process = Start-Process `
    -FilePath $Python `
    -ArgumentList "run_smri_server.py" `
    -WorkingDirectory $ProjectDir `
    -WindowStyle Hidden `
    -PassThru

Write-SmriLog "[SMRI] PID servidor: $($Process.Id)"

Write-SmriLog "[SMRI] Proceso de servidor solicitado: $(Get-Date)"
