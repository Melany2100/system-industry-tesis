# Guia de implementacion local - Sistema SMRI

Esta guia describe como instalar y ejecutar localmente el Sistema de Monitoreo
de Riesgos Industriales y Eventos (SMRI). El proyecto es una aplicacion Django
con modulos de autenticacion, camaras, eventos de seguridad, informes,
reconocimiento facial, evidencias en `media/` y deteccion con modelos YOLO.

El flujo local recomendado para pruebas es:

```text
Camaras IP / webcam -> Django local -> PostgreSQL local -> Interfaz web local
```

Para un escenario donde la maquina local procesa video y la VPS muestra los
eventos, revisar tambien:

```text
docs/ENV_MAQUINA_LOCAL_ENVIA_EVENTOS_VPS.md
```

## 1. Requisitos previos

### Sistema operativo recomendado

- Windows 10/11 para pruebas locales.
- Tambien puede ejecutarse en Linux, ajustando los comandos de PowerShell a
  Bash.

### Software necesario

- Python 3.10 o superior.
- PostgreSQL 14 o superior.
- Git.
- FFmpeg, recomendado para diagnosticar camaras RTSP.
- Visual C++ Build Tools o herramientas equivalentes si se compilan paquetes
  como `dlib`.

### Recursos recomendados

- 8 GB RAM como minimo para pruebas con YOLO.
- 16 GB RAM recomendado si se usaran varias camaras.
- CPU moderna. GPU opcional.
- Red local estable hacia las camaras IP.

## 2. Preparar el proyecto

Ubicarse en la carpeta del proyecto:

```powershell
cd C:\Users\HP\Documents\TITULACION\proyecto\v2\system-industry-tesis
```

Crear y activar el entorno virtual:

```powershell
python -m venv .venv
.\.venv\Scripts\activate
```

Actualizar herramientas base:

```powershell
python -m pip install --upgrade pip setuptools wheel
```

Instalar dependencias:

```powershell
pip install -r requirements.txt
```

Si la instalacion falla en `dlib` o `face-recognition`, instalar primero las
herramientas de compilacion de Windows y volver a ejecutar `pip install`.

## 3. Crear la base de datos local

El archivo `config/settings.py` esta configurado para PostgreSQL mediante
variables de entorno. Crear una base y un usuario local.

Entrar a `psql` como usuario administrador de PostgreSQL:

```powershell
psql -U postgres
```

Ejecutar:

```sql
CREATE DATABASE smri_local;
CREATE USER smri_user WITH PASSWORD 'smri_local_password';
ALTER ROLE smri_user SET client_encoding TO 'utf8';
ALTER ROLE smri_user SET default_transaction_isolation TO 'read committed';
ALTER ROLE smri_user SET timezone TO 'America/Guayaquil';
GRANT ALL PRIVILEGES ON DATABASE smri_local TO smri_user;
\c smri_local
GRANT ALL ON SCHEMA public TO smri_user;
\q
```

> Si PostgreSQL esta instalado con otro usuario, adaptar `DB_USER` y
> `DB_PASSWORD` en el archivo `.env`.

## 4. Configurar `.env`

Crear un archivo `.env` en la raiz del proyecto:

```powershell
New-Item -ItemType File .env
```

Contenido sugerido para ejecucion local:

```env
SECRET_KEY=cambiar_por_una_clave_larga_y_segura
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1,0.0.0.0
CSRF_TRUSTED_ORIGINS=http://localhost:8000,http://127.0.0.1:8000

DB_NAME=smri_local
DB_USER=smri_user
DB_PASSWORD=smri_local_password
DB_HOST=127.0.0.1
DB_PORT=5432

SESSION_COOKIE_SECURE=False
CSRF_COOKIE_SECURE=False
SECURE_SSL_REDIRECT=False

EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USE_TLS=True
EMAIL_USE_SSL=False
EMAIL_HOST_USER=
EMAIL_HOST_PASSWORD=
DEFAULT_FROM_EMAIL=no-reply@smri.local
EMAIL_TIMEOUT=10

MEDIA_URL=/media/
STATIC_URL=/static/

RTSP_CAMERA_URL=rtsp://usuario:password@IP_CAMARA:554/stream1
RTSP_CAMERA_URL_FAST=rtsp://usuario:password@IP_CAMARA:554/stream2

```

Para generar una clave secreta:

```powershell
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

Copiar el resultado en `SECRET_KEY`.

## 5. Verificar modelos y pesos

El sistema espera modelos YOLO en rutas locales. Verificar que existan:

```powershell
Get-ChildItem camera
Get-ChildItem camera\weights
```

Rutas usadas por la configuracion:

```text
camera/weights/yolov8s.pt
camera/weights/yolov8s-pose.pt
camera/yolov3-tiny.weights
camera/yolov3-tiny.cfg
camera/coco.names
```

Si los archivos `.pt` estan en la raiz del proyecto, moverlos a
`camera/weights/` o ajustar las rutas en `config/settings.py`. Ejemplo:

```powershell
New-Item -ItemType Directory -Force camera\weights
Move-Item yolov8s.pt camera\weights\yolov8s.pt
Move-Item yolov8s-pose.pt camera\weights\yolov8s-pose.pt
```

No mover archivos si ya existen en `camera/weights/`.

## 6. Aplicar migraciones

Con el entorno virtual activo:

```powershell
python manage.py check
python manage.py migrate
```

Crear datos iniciales de prueba:

```powershell
python manage.py seed_demo --username admin --password admin12345 --events 8
```

Tambien se puede crear un superusuario manual:

```powershell
python manage.py createsuperuser
```

## 7. Ejecutar el sistema

Opcion rapida en Windows:

```powershell
.\start_smri.bat
```

Este script:

- Activa `.venv` si existe.
- Define `SMRI_AUTOSTART_CAMERAS=1`.
- Define `SMRI_CAMERA_AUTOSTART_FPS=8`.
- Ejecuta migraciones pendientes.
- Inicia Django en `0.0.0.0:8000`.

Opcion manual:

```powershell
$env:SMRI_AUTOSTART_CAMERAS="1"
$env:SMRI_CAMERA_AUTOSTART_FPS="8"
python manage.py runserver 0.0.0.0:8000
```

Abrir en el navegador:

```text
http://127.0.0.1:8000/login/
```

Credenciales demo, si se ejecuto `seed_demo`:

```text
Usuario: admin
Clave: admin12345
```

## 8. Rutas principales

```text
/login/                 Inicio de sesion
/                       Inicio protegido
/dashboard/             Panel principal
/camera/                Modulo de camaras
/informes/              Informes
/settings/              Configuracion de usuarios/perfil
/admin/                 Administracion Django
/api/                   API del modulo de camara
```

Eventos de seguridad:

```text
/camera/security-events/
/camera/security-events/<id>/resolve/
```

## 9. Registrar camaras

Entrar al admin:

```text
http://127.0.0.1:8000/admin/
```

Crear registros en el modelo `Camera`.

Ejemplo con camara RTSP:

```text
Nombre: VIGI C240 - Area 1
Source: rtsp://admin:CAMBIAR_PASSWORD_CAMARA@192.168.10.201:554/stream2
Ubicacion: Area 1
Activa: Si
```

Ejemplo con webcam local:

```text
Nombre: Webcam local
Source: 0
Ubicacion: Pruebas
Activa: Si
```

Recomendaciones:

- Usar `stream2` para deteccion si la camara lo soporta, porque suele ser mas
  liviano.
- Mantener las camaras y la PC en la misma red local o VLAN.
- Evitar exponer RTSP a Internet.
- Verificar que usuario, clave, IP y stream sean correctos.

## 10. Probar camaras RTSP

Con FFmpeg instalado:

```powershell
ffprobe "rtsp://usuario:password@IP_CAMARA:554/stream2"
```

O capturar unos segundos:

```powershell
ffmpeg -rtsp_transport tcp -i "rtsp://usuario:password@IP_CAMARA:554/stream2" -t 5 -f null -
```

Si falla:

- Confirmar que la PC hace ping a la camara.
- Validar credenciales.
- Probar otra ruta de stream: `stream1`, `stream2`, `h264Preview_01_main`,
  `h264Preview_01_sub`.
- Revisar firewall local.
- Confirmar que la camara permite mas de una conexion simultanea.

## 11. Evidencias y archivos media

Las imagenes de eventos se guardan en:

```text
media/
```

En desarrollo, Django sirve `media/` automaticamente porque `DEBUG=True`.
Verificar que la carpeta exista:

```powershell
New-Item -ItemType Directory -Force media
```

Si se procesa localmente y se quiere visualizar en una VPS, hay que sincronizar
`media/` hacia el servidor o implementar un endpoint que reciba evento +
evidencia.

## 12. Correo de alertas

Para pruebas locales se recomienda:

```env
EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend
```

Asi los correos aparecen en la terminal y no se envian realmente.

Para usar SMTP:

```env
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USE_TLS=True
EMAIL_USE_SSL=False
EMAIL_HOST_USER=cuenta.smtp@example.com
EMAIL_HOST_PASSWORD=clave_o_token_de_aplicacion
DEFAULT_FROM_EMAIL=Sistema SMRI <cuenta.smtp@example.com>
```

En Gmail normalmente se necesita una clave de aplicacion.

## 13. Validar funcionamiento

Ejecutar:

```powershell
python manage.py check
python manage.py showmigrations
```

Validar conexion a la base:

```powershell
python manage.py shell
```

Dentro del shell:

```python
from core_apps.camera.models import Camera, SecurityEvent
Camera.objects.count()
SecurityEvent.objects.count()
```

Crear un evento manual de prueba:

```python
SecurityEvent.objects.create(
    event_type="dangerous_object",
    severity="ALTO",
    details="Evento local de prueba",
)
```

Luego revisar la interfaz de eventos/informes.

## 14. Ejecucion local conectada a una VPS

Si la PC local procesa camaras pero la base de datos vive en una VPS, no usar
PostgreSQL local. En su lugar:

1. Abrir un tunel SSH:

```powershell
ssh -N -L 15432:127.0.0.1:5432 usuario_vps@IP_PUBLICA_VPS
```

2. Configurar `.env`:

```env
DB_NAME=smri_prod
DB_USER=smri_user
DB_PASSWORD=CAMBIAR_PASSWORD_POSTGRES_VPS
DB_HOST=127.0.0.1
DB_PORT=15432
```

3. Iniciar el sistema local:

```powershell
.\start_smri.bat
```

Los eventos se escribiran en la base de la VPS. Para que las evidencias tambien
se vean desde la VPS, sincronizar `media/` o usar un almacenamiento compartido.

## 15. Problemas comunes

### `SECRET_KEY` vacio

Si Django no arranca, revisar que `.env` tenga:

```env
SECRET_KEY=valor_largo_y_seguro
```

### Error de conexion a PostgreSQL

Revisar:

```env
DB_NAME=
DB_USER=
DB_PASSWORD=
DB_HOST=
DB_PORT=
```

Probar conexion directa:

```powershell
psql -U smri_user -h 127.0.0.1 -p 5432 -d smri_local
```

### Error de `ALLOWED_HOSTS`

Si se accede desde otra PC de la red, agregar la IP local:

```env
ALLOWED_HOSTS=localhost,127.0.0.1,0.0.0.0,192.168.10.10
CSRF_TRUSTED_ORIGINS=http://192.168.10.10:8000,http://127.0.0.1:8000
```

Reiniciar Django.

### La camara no muestra video

Validar primero con `ffprobe` o VLC. Si ahi tampoco funciona, el problema esta
en red, credenciales o ruta RTSP.

### Alto consumo de CPU

Reducir carga:

- Usar `stream2`.
- Bajar `SMRI_CAMERA_AUTOSTART_FPS`.
- Procesar menos camaras.
- Aumentar intervalos de deteccion en `config/settings.py`.

### No aparecen imagenes de evidencia

Verificar:

- Que el evento tenga `image_path`.
- Que el archivo exista dentro de `media/`.
- Que `DEBUG=True` en local.
- Que `MEDIA_URL=/media/`.

## 16. Checklist final

- [ ] Python instalado.
- [ ] PostgreSQL instalado y base creada.
- [ ] `.venv` creado y activado.
- [ ] Dependencias instaladas con `pip install -r requirements.txt`.
- [ ] `.env` creado con `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS` y `DB_*`.
- [ ] Modelos YOLO ubicados en las rutas esperadas.
- [ ] Migraciones aplicadas.
- [ ] Usuario demo o superusuario creado.
- [ ] Camaras registradas en el admin.
- [ ] RTSP probado con `ffprobe`, VLC o FFmpeg.
- [ ] Servidor iniciado en `http://127.0.0.1:8000/login/`.
- [ ] Eventos y evidencias verificados desde la interfaz.
