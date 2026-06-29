# Guia completa de instalacion local - Sistema SMRI

Esta guia explica como instalar el Sistema de Monitoreo de Riesgos Industriales
y Eventos (SMRI) en una maquina local Windows, dejarlo conectado a camaras RTSP,
crear un usuario administrador y configurar el arranque automatico cuando se
reinicie la maquina.

## 1. Requisitos de la maquina

### Hardware recomendado

- CPU Intel i5/Ryzen 5 o superior.
- 8 GB RAM minimo.
- 16 GB RAM recomendado si se procesan varias camaras.
- Disco con al menos 20 GB libres.
- Conexion estable por cable Ethernet hacia las camaras.

### Software necesario

Instalar antes de configurar el proyecto:

- Python 3.10.
- PostgreSQL 14 o superior.
- Git.
- FFmpeg opcional, pero recomendado para probar RTSP.
- Visual C++ Build Tools, recomendado si `dlib` o `face-recognition` fallan al instalar.

## 2. Copiar el proyecto

Copiar la carpeta del proyecto en la nueva maquina, por ejemplo:

```text
C:\SMRI\system-industry-tesis
```

Entrar desde PowerShell o CMD:

```powershell
cd C:\SMRI\system-industry-tesis
```

La carpeta debe incluir al menos:

```text
manage.py
config/
core_apps/
templates/
static/
camera/
requirements.txt
start_smri.bat
start_smri_autostart.bat
start_smri_hidden.vbs
instalar_inicio_windows_smri.bat
stop_smri.bat
```

## 3. Crear entorno virtual

```powershell
python -m venv .venv
.\.venv\Scripts\activate
python -m pip install --upgrade pip setuptools wheel
```

## 4. Instalar dependencias

El proyecto usa `requirements.txt`. Instalar:

```powershell
pip install -r requirements.txt
```

Si falla `dlib` o `face-recognition`:

- Instalar Visual C++ Build Tools.
- Reiniciar la terminal.
- Volver a ejecutar `pip install -r requirements.txt`.

## 5. Crear base de datos PostgreSQL

Abrir una terminal y entrar a PostgreSQL:

```powershell
psql -U postgres
```

Crear base y usuario:

```sql
CREATE DATABASE smri_local;
CREATE USER smri_user WITH PASSWORD 'CAMBIAR_PASSWORD_LOCAL';
ALTER ROLE smri_user SET client_encoding TO 'utf8';
ALTER ROLE smri_user SET default_transaction_isolation TO 'read committed';
ALTER ROLE smri_user SET timezone TO 'America/Guayaquil';
GRANT ALL PRIVILEGES ON DATABASE smri_local TO smri_user;
\c smri_local
GRANT ALL ON SCHEMA public TO smri_user;
\q
```

Verificar que el servicio de PostgreSQL quede automatico:

1. Presionar `Win + R`.
2. Escribir `services.msc`.
3. Buscar `postgresql`.
4. Tipo de inicio: `Automatico`.

## 6. Crear archivo `.env`

Crear `.env` en la raiz del proyecto:

```powershell
New-Item -ItemType File .env
```

Contenido recomendado:

```env
SECRET_KEY=cambiar_por_una_clave_larga_y_segura
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1,0.0.0.0
CSRF_TRUSTED_ORIGINS=http://localhost:8000,http://127.0.0.1:8000

DB_NAME=smri_local
DB_USER=smri_user
DB_PASSWORD=CAMBIAR_PASSWORD_LOCAL
DB_HOST=127.0.0.1
DB_PORT=5432

SESSION_COOKIE_SECURE=False
CSRF_COOKIE_SECURE=False
SECURE_SSL_REDIRECT=False

EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USE_TLS=True
EMAIL_USE_SSL=False
EMAIL_HOST_USER=tu_correo@gmail.com
EMAIL_HOST_PASSWORD=clave_de_aplicacion_gmail
DEFAULT_FROM_EMAIL=Sistema SMRI <tu_correo@gmail.com>
EMAIL_TIMEOUT=10

MEDIA_URL=/media/
STATIC_URL=/static/

RTSP_TRANSPORT=udp
RTSP_INITIAL_FRAME_TIMEOUT_SECONDS=15
RTSP_STALE_FRAME_SECONDS=2

RISK_YOLO_FRAME_INTERVAL=30
FACE_RECOGNITION_FRAME_INTERVAL=16
FACE_DETECTION_FRAME_INTERVAL=12
FACE_ANALYSIS_WIDTH=480
PPE_FRAME_INTERVAL=45
PPE_INFERENCE_IMGSZ=960
DETECT_SHARP_EVERY_N_FRAMES=6
DETECT_POSE_EVERY_N_FRAMES=35
DETECT_PHONE_EVERY_N_FRAMES=4
EVENT_COOLDOWN_SECONDS=10
```

Generar una clave segura para `SECRET_KEY`:

```powershell
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

Copiar el resultado en `SECRET_KEY`.

## 7. Migrar la base de datos

Con `.venv` activo:

```powershell
python manage.py check
python manage.py migrate
```

## 8. Crear usuario administrador

Opcion recomendada:

```powershell
python manage.py createsuperuser
```

Completar:

```text
Username: admin
Email address: correo_admin@empresa.com
Password: una_clave_segura
```

Opcion demo:

```powershell
python manage.py seed_demo --username admin --password admin12345 --events 8
```

No usar `admin12345` en una maquina final.

## 9. Verificar modelos YOLO

Revisar que existan los modelos necesarios:

```powershell
Get-ChildItem camera
Get-ChildItem camera\weights
```

Rutas esperadas:

```text
camera/weights/yolov8s.pt
camera/weights/yolov8s-pose.pt
camera/yolov3-tiny.weights
camera/yolov3-tiny.cfg
camera/coco.names
```

Si los modelos `.pt` estan en la raiz, crear `camera\weights` y moverlos:

```powershell
New-Item -ItemType Directory -Force camera\weights
Move-Item yolov8s.pt camera\weights\yolov8s.pt
Move-Item yolov8s-pose.pt camera\weights\yolov8s-pose.pt
```

## 10. Primer arranque manual

Ejecutar:

```powershell
.\start_smri.bat
```

Abrir:

```text
http://127.0.0.1:8000/login/
```

Si inicia correctamente, detener con:

```text
CTRL + C
```

o usando:

```powershell
.\stop_smri.bat
```

## 11. Registrar camaras

Entrar al admin:

```text
http://127.0.0.1:8000/admin/
```

Ir a:

```text
Camera > Cameras
```

Crear camara RTSP:

```text
Nombre: VIGI C240 1
Source: rtsp://admin:CLAVE_CAMARA@IP_CAMARA:554/stream2
Ubicacion: Area 1
Activa: Si
```

Recomendacion importante:

- Usar `stream2` para el sistema de deteccion.
- Evitar `stream1` si la imagen va lenta o se congela.
- `stream1` suele ser alta resolucion y consume mas CPU/red.
- `stream2` suele ser substream liviano y reduce retraso.

Ejemplo:

```text
rtsp://admin:CLAVE@192.168.10.201:554/stream2
```

Si se usa webcam local:

```text
Nombre: Webcam local
Source: 0
Ubicacion: Pruebas
Activa: Si
```

Desactivar o eliminar camaras que no se usen. No dejar activas camaras antiguas
tipo `push://`, porque el sistema local ya no usa ese flujo.

## 12. Probar RTSP antes de usarlo

Con FFmpeg:

```powershell
ffprobe "rtsp://admin:CLAVE@IP_CAMARA:554/stream2"
```

Con VLC:

```text
Medio > Abrir ubicacion de red > rtsp://...
```

Si VLC o FFmpeg tambien se retrasan, el problema esta en red, camara, IP,
credenciales o tipo de stream.

## 13. Arranque automatico oculto con Windows

Ejecutar una sola vez:

```powershell
.\instalar_inicio_windows_smri.bat
```

Esto registra un archivo en:

```text
%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
```

El arranque automatico usa:

```text
start_smri_hidden.vbs
```

Ese archivo inicia:

```text
start_smri_autostart.bat
```

sin dejar la ventana negra visible.

Cuando Windows reinicie e inicie sesion, el sistema levantara solo.

## 14. Verificar que arranco

Abrir:

```text
http://127.0.0.1:8000/login/
```

Revisar log:

```text
logs/smri_autostart.log
```

En PowerShell:

```powershell
Get-Content logs\smri_autostart.log -Tail 80
```

## 15. Apagar el sistema

Ejecutar:

```powershell
.\stop_smri.bat
```

Este script busca el proceso que escucha en el puerto `8000` y lo detiene.

## 16. Ajustes para que la camara no se congele

Primero revisar en admin:

- Dejar activas solo las camaras necesarias.
- Eliminar o desactivar camaras `push://`.
- Usar `stream2` en camaras RTSP.
- Si hay webcam `source=0` y no se usa, desactivarla.

Luego ajustar `.env` si aun hay retraso:

```env
RTSP_TRANSPORT=udp
RISK_YOLO_FRAME_INTERVAL=40
FACE_RECOGNITION_FRAME_INTERVAL=20
PPE_FRAME_INTERVAL=60
DETECT_PHONE_EVERY_N_FRAMES=6
```

Si la red pierde imagen con UDP, probar:

```env
RTSP_TRANSPORT=tcp
```

Tambien se puede bajar el FPS de arranque en:

```text
start_smri_autostart.bat
start_smri.bat
```

Linea:

```bat
set SMRI_CAMERA_AUTOSTART_FPS=2
```

## 17. Correos

Para Gmail se necesita una clave de aplicacion:

```env
EMAIL_HOST_USER=tu_correo@gmail.com
EMAIL_HOST_PASSWORD=clave_de_aplicacion_gmail
DEFAULT_FROM_EMAIL=Sistema SMRI <tu_correo@gmail.com>
```

Si aparece:

```text
WinError 10013
```

Windows, antivirus o firewall esta bloqueando la salida SMTP. Revisar permisos
para Python o permitir salida al puerto `587`.

Para pruebas sin enviar correos reales:

```env
EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend
```

## 18. Gestion desde Django Admin

Desde `/admin/` se puede:

- Crear usuarios administradores.
- Crear o eliminar camaras.
- Activar/desactivar camaras.
- Crear o eliminar personas autorizadas.
- Activar/desactivar personas autorizadas.
- Eliminar informes seleccionados.
- Eliminar informes con mas de 30, 60 o 90 dias.

Para crear otro administrador:

1. Entrar a `/admin/`.
2. Ir a `Authentication and Authorization > Users`.
3. Crear usuario.
4. Marcar `Staff status`.
5. Agregar al grupo `Administrador` si corresponde.

## 19. Checklist final

- [ ] Python instalado.
- [ ] PostgreSQL instalado y en modo automatico.
- [ ] Proyecto copiado.
- [ ] `.venv` creado.
- [ ] `pip install -r requirements.txt` ejecutado.
- [ ] `.env` creado.
- [ ] Base `smri_local` creada.
- [ ] `python manage.py migrate` ejecutado.
- [ ] Usuario admin creado.
- [ ] Camaras RTSP registradas con `stream2`.
- [ ] Camaras no usadas desactivadas.
- [ ] `start_smri.bat` probado manualmente.
- [ ] `instalar_inicio_windows_smri.bat` ejecutado.
- [ ] Sistema probado tras reiniciar Windows.
- [ ] Log revisado en `logs/smri_autostart.log`.

## 20. Problemas comunes

### El sistema no abre

Revisar:

```powershell
Get-Content logs\smri_autostart.log -Tail 100
```

Verificar puerto:

```powershell
netstat -ano | findstr ":8000"
```

### La camara se congela

- Cambiar `stream1` por `stream2`.
- Dejar activa solo una camara para probar.
- Bajar `SMRI_CAMERA_AUTOSTART_FPS` a `1`.
- Subir `PPE_FRAME_INTERVAL`.
- Usar cable Ethernet.

### No conecta PostgreSQL

Verificar servicio de PostgreSQL y `.env`:

```env
DB_NAME=smri_local
DB_USER=smri_user
DB_PASSWORD=CAMBIAR_PASSWORD_LOCAL
DB_HOST=127.0.0.1
DB_PORT=5432
```

### No llegan correos

- Revisar clave de aplicacion Gmail.
- Revisar firewall.
- Probar temporalmente `EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend`.

### Pantalla negra visible

Ejecutar nuevamente:

```powershell
.\instalar_inicio_windows_smri.bat
```

Debe apuntar a `start_smri_hidden.vbs`.
