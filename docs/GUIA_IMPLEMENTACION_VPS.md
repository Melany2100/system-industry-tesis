# Guia de implementacion en una VPS - Sistema SMRI

Esta guia describe una instalacion recomendada del sistema SMRI en una VPS
Linux usando Ubuntu Server, PostgreSQL, Gunicorn, Nginx y systemd.

El proyecto es una aplicacion Django con modulos de autenticacion, informes,
eventos de seguridad, camaras RTSP, reconocimiento facial y deteccion con
modelos YOLO. Por esa razon, la VPS debe tener recursos suficientes para
procesamiento de video.

## 1. Requisitos recomendados

### VPS minima para pruebas

- Ubuntu Server 22.04 LTS o 24.04 LTS.
- 2 vCPU.
- 4 GB RAM.
- 40 GB de disco.
- Acceso SSH con usuario sudo.

### VPS recomendada para uso con camaras y YOLO

- 4 vCPU o mas.
- 8 GB RAM o mas.
- 80 GB de disco o mas.
- GPU opcional si se va a procesar video en tiempo real con mayor carga.
- Buena conectividad hacia las camaras IP/RTSP.

> Nota: si las camaras estan dentro de una red local privada, la VPS debe poder
> llegar a esa red mediante VPN, tunel seguro o reglas de red apropiadas. No se
> recomienda exponer camaras RTSP directamente a Internet.

## 2. Preparar el servidor

Conectarse por SSH:

```bash
ssh usuario@TU_IP_DEL_SERVIDOR
```

Actualizar paquetes:

```bash
sudo apt update
sudo apt upgrade -y
```

Instalar dependencias del sistema:

```bash
sudo apt install -y \
  python3 python3-venv python3-pip python3-dev \
  build-essential cmake pkg-config \
  git nginx postgresql postgresql-contrib \
  libpq-dev \
  libgl1 libglib2.0-0 libsm6 libxext6 libxrender1 \
  ffmpeg
```

Dependencias utiles para `face-recognition` y `dlib`:

```bash
sudo apt install -y \
  libopenblas-dev liblapack-dev \
  libboost-all-dev
```

## 3. Crear usuario de despliegue

Crear un usuario separado para ejecutar la aplicacion:

```bash
sudo adduser smri
sudo usermod -aG sudo smri
```

Entrar con ese usuario:

```bash
su - smri
```

## 4. Descargar el proyecto

Ubicacion sugerida:

```bash
mkdir -p /home/smri/apps
cd /home/smri/apps
git clone URL_DEL_REPOSITORIO system-industry-tesis
cd system-industry-tesis
```

Si el proyecto se copia manualmente, asegurese de incluir:

- `manage.py`
- `config/`
- `core_apps/`
- `templates/`
- `static/`
- `camera/`
- `requirements.txt`
- modelos `.pt` necesarios, especialmente:
  - `camera/weights/yolov8s.pt`
  - `camera/weights/yolov8s-pose.pt`
  - `camera/weights/yolov8n.pt`
  - `camera/ppe.pt`
  - `camera/yolov3-tiny.weights`
  - `camera/yolov3-tiny.cfg`
  - `camera/coco.names`

## 5. Crear entorno virtual e instalar Python packages

```bash
cd /home/smri/apps/system-industry-tesis
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
pip install -r requirements.txt
```

Si `dlib` o `face-recognition` fallan al instalar, normalmente falta memoria o
dependencias nativas. En VPS pequenas puede ayudar crear swap temporal:

```bash
sudo fallocate -l 4G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
```

Para dejar el swap permanente, agregar esta linea en `/etc/fstab`:

```text
/swapfile none swap sw 0 0
```

## 6. Configurar PostgreSQL

Entrar a PostgreSQL:

```bash
sudo -u postgres psql
```

Crear base de datos y usuario:

```sql
CREATE DATABASE smri_prod;
CREATE USER smri_user WITH PASSWORD 'CAMBIAR_PASSWORD_SEGURO';
ALTER ROLE smri_user SET client_encoding TO 'utf8';
ALTER ROLE smri_user SET default_transaction_isolation TO 'read committed';
ALTER ROLE smri_user SET timezone TO 'America/Guayaquil';
GRANT ALL PRIVILEGES ON DATABASE smri_prod TO smri_user;
\q
```

En PostgreSQL 15 o superior, puede ser necesario dar permisos sobre el esquema
`public`:

```bash
sudo -u postgres psql -d smri_prod
```

```sql
GRANT ALL ON SCHEMA public TO smri_user;
\q
```

## 7. Configurar variables de entorno

Crear el archivo `.env` en la raiz del proyecto:

```bash
cd /home/smri/apps/system-industry-tesis
nano .env
```

Contenido sugerido:

```env
DJANGO_SECRET_KEY=CAMBIAR_SECRET_KEY_LARGA_Y_ALEATORIA
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=TU_DOMINIO,TU_IP_DEL_SERVIDOR,localhost,127.0.0.1
CSRF_TRUSTED_ORIGINS=https://TU_DOMINIO

DB_NAME=smri_prod
DB_USER=smri_user
DB_PASSWORD=CAMBIAR_PASSWORD_SEGURO
DB_HOST=127.0.0.1
DB_PORT=5432

EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USE_TLS=True
EMAIL_USE_SSL=False
EMAIL_HOST_USER=cuenta.smtp@example.com
EMAIL_HOST_PASSWORD=clave_o_token_de_aplicacion
DEFAULT_FROM_EMAIL=Sistema SMRI <cuenta.smtp@example.com>
EMAIL_TIMEOUT=10

RTSP_CAMERA_URL=rtsp://usuario:password@IP_CAMARA:554/stream1
RTSP_CAMERA_URL_FAST=rtsp://usuario:password@IP_CAMARA:554/stream2
```

Generar una `SECRET_KEY` segura:

```bash
python - <<'PY'
from django.core.management.utils import get_random_secret_key
print(get_random_secret_key())
PY
```

Proteger el archivo:

```bash
chmod 600 .env
```

## 8. Ajustes necesarios en `config/settings.py`

La configuracion actual del proyecto esta orientada a desarrollo:

- `SECRET_KEY` esta fija en el codigo.
- `DEBUG = True`.
- `ALLOWED_HOSTS = []`.
- La base PostgreSQL tiene credenciales fijas.
- No existe `STATIC_ROOT`, necesario para `collectstatic`.
- Las cookies seguras estan desactivadas.

Antes de publicar en una VPS, se recomienda adaptar `config/settings.py` para
leer esos valores desde `.env`. Ejemplo:

```python
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "cambiar-en-produccion")
DEBUG = os.getenv("DJANGO_DEBUG", "False").lower() == "true"
ALLOWED_HOSTS = [
    host.strip()
    for host in os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if host.strip()
]

CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.getenv("CSRF_TRUSTED_ORIGINS", "").split(",")
    if origin.strip()
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("DB_NAME", "smri_prod"),
        "USER": os.getenv("DB_USER", "smri_user"),
        "PASSWORD": os.getenv("DB_PASSWORD", ""),
        "HOST": os.getenv("DB_HOST", "127.0.0.1"),
        "PORT": os.getenv("DB_PORT", "5432"),
    }
}

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_SSL_REDIRECT = not DEBUG
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
```

Si se va a probar primero solo con IP y HTTP, puede dejarse
`SECURE_SSL_REDIRECT=False` temporalmente. Para produccion real con dominio,
usar HTTPS.

## 9. Migraciones, estaticos y usuario inicial

Activar el entorno virtual:

```bash
cd /home/smri/apps/system-industry-tesis
source .venv/bin/activate
```

Ejecutar verificaciones:

```bash
python manage.py check
python manage.py migrate
python manage.py collectstatic --noinput
```

Crear usuario administrador:

```bash
python manage.py createsuperuser
```

Opcionalmente, cargar datos demo:

```bash
python manage.py seed_demo --username admin --password 'CAMBIAR_PASSWORD' --events 8
```

> No usar `admin12345` en produccion.

## 10. Probar con Gunicorn

Instalar Gunicorn si no esta en `requirements.txt`:

```bash
source .venv/bin/activate
pip install gunicorn
```

Probar el arranque:

```bash
gunicorn --bind 127.0.0.1:8001 config.wsgi:application
```

En otra terminal:

```bash
curl -I http://127.0.0.1:8001/login/
```

Detener Gunicorn con `Ctrl+C`.

## 11. Crear servicio systemd

Crear el archivo:

```bash
sudo nano /etc/systemd/system/smri.service
```

Contenido:

```ini
[Unit]
Description=SMRI Django Gunicorn service
After=network.target postgresql.service

[Service]
User=smri
Group=www-data
WorkingDirectory=/home/smri/apps/system-industry-tesis
EnvironmentFile=/home/smri/apps/system-industry-tesis/.env
ExecStart=/home/smri/apps/system-industry-tesis/.venv/bin/gunicorn \
  --workers 2 \
  --timeout 180 \
  --bind unix:/run/smri.sock \
  config.wsgi:application

RuntimeDirectory=smri
RuntimeDirectoryMode=0755
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Activar el servicio:

```bash
sudo systemctl daemon-reload
sudo systemctl enable smri
sudo systemctl start smri
sudo systemctl status smri
```

Ver logs:

```bash
sudo journalctl -u smri -f
```

## 12. Configurar Nginx

Crear archivo de sitio:

```bash
sudo nano /etc/nginx/sites-available/smri
```

Contenido para HTTP inicial:

```nginx
server {
    listen 80;
    server_name TU_DOMINIO TU_IP_DEL_SERVIDOR;

    client_max_body_size 50M;

    location /static/ {
        alias /home/smri/apps/system-industry-tesis/staticfiles/;
    }

    location /media/ {
        alias /home/smri/apps/system-industry-tesis/media/;
    }

    location / {
        proxy_pass http://unix:/run/smri.sock;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 300;
        proxy_connect_timeout 300;
        proxy_send_timeout 300;
    }
}
```

Habilitar el sitio:

```bash
sudo ln -s /etc/nginx/sites-available/smri /etc/nginx/sites-enabled/smri
sudo nginx -t
sudo systemctl reload nginx
```

Abrir en navegador:

```text
http://TU_IP_DEL_SERVIDOR/login/
```

## 13. Activar HTTPS con Certbot

Si ya tiene un dominio apuntando a la VPS:

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d TU_DOMINIO
```

Probar renovacion:

```bash
sudo certbot renew --dry-run
```

Luego confirmar en `.env`:

```env
DJANGO_DEBUG=False
CSRF_TRUSTED_ORIGINS=https://TU_DOMINIO
```

Reiniciar:

```bash
sudo systemctl restart smri
sudo systemctl reload nginx
```

## 14. Configurar firewall

Con UFW:

```bash
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw enable
sudo ufw status
```

No abrir PostgreSQL al publico salvo que sea estrictamente necesario. Si se usa
una base externa, limitar por IP y usar credenciales seguras.

## 15. Camaras RTSP y red

Validar que la VPS puede ver la camara:

```bash
ffprobe rtsp://usuario:password@IP_CAMARA:554/stream1
```

O probar unos segundos con FFmpeg:

```bash
ffmpeg -rtsp_transport tcp -i "rtsp://usuario:password@IP_CAMARA:554/stream1" -t 5 -f null -
```

Recomendaciones:

- Usar VPN site-to-site o WireGuard si las camaras estan en una red local.
- Evitar contrasenas RTSP dentro del codigo.
- Usar streams secundarios de menor resolucion para deteccion rapida.
- Verificar que la camara permita conexiones simultaneas.
- Revisar la latencia entre la VPS y la red de camaras.

## 16. Modelos YOLO y archivos de peso

El proyecto usa rutas locales como:

```text
camera/weights/yolov8s.pt
camera/weights/yolov8s-pose.pt
camera/weights/yolov8n.pt
camera/ppe.pt
camera/yolov3-tiny.weights
camera/yolov3-tiny.cfg
camera/coco.names
```

Verificar que existan:

```bash
ls -lh camera/weights/
ls -lh camera/
```

Si se suben modelos grandes por separado, usar `scp` o `rsync`:

```bash
rsync -avz camera/weights/ smri@TU_IP_DEL_SERVIDOR:/home/smri/apps/system-industry-tesis/camera/weights/
```

Dar permisos:

```bash
sudo chown -R smri:www-data /home/smri/apps/system-industry-tesis
chmod -R u+rwX,g+rX /home/smri/apps/system-industry-tesis/media
```

## 17. Permisos de media y evidencias

El sistema guarda imagenes/evidencias en `media/`. Crear carpeta si no existe:

```bash
mkdir -p /home/smri/apps/system-industry-tesis/media
sudo chown -R smri:www-data /home/smri/apps/system-industry-tesis/media
chmod -R 775 /home/smri/apps/system-industry-tesis/media
```

Se recomienda planificar retencion de evidencias y backups, porque el video y
las imagenes pueden crecer rapidamente.

## 18. Backups

Crear carpeta de backups:

```bash
mkdir -p /home/smri/backups
chmod 700 /home/smri/backups
```

Backup manual de PostgreSQL:

```bash
pg_dump -U smri_user -h 127.0.0.1 smri_prod > /home/smri/backups/smri_$(date +%F).sql
```

Backup de media:

```bash
tar -czf /home/smri/backups/media_$(date +%F).tar.gz /home/smri/apps/system-industry-tesis/media
```

Restaurar base:

```bash
psql -U smri_user -h 127.0.0.1 smri_prod < backup.sql
```

## 19. Actualizar una version nueva del proyecto

```bash
cd /home/smri/apps/system-industry-tesis
git pull
source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic --noinput
sudo systemctl restart smri
sudo systemctl reload nginx
```

Verificar logs:

```bash
sudo journalctl -u smri -n 100 --no-pager
```

## 20. Comandos de diagnostico

Estado de servicios:

```bash
sudo systemctl status smri
sudo systemctl status nginx
sudo systemctl status postgresql
```

Logs de la aplicacion:

```bash
sudo journalctl -u smri -f
```

Logs de Nginx:

```bash
sudo tail -f /var/log/nginx/access.log
sudo tail -f /var/log/nginx/error.log
```

Verificar configuracion Django:

```bash
source .venv/bin/activate
python manage.py check --deploy
```

Verificar conexion a PostgreSQL:

```bash
psql -U smri_user -h 127.0.0.1 -d smri_prod
```

Verificar socket Gunicorn:

```bash
ls -lh /run/smri.sock
```

## 21. Problemas comunes

### Error 502 Bad Gateway

Revisar:

```bash
sudo systemctl status smri
sudo journalctl -u smri -n 100 --no-pager
sudo nginx -t
```

Causas comunes:

- Gunicorn no arranco.
- Error en `.env`.
- Migraciones pendientes.
- Permisos incorrectos en `/run/smri.sock`.

### Archivos estaticos no cargan

Ejecutar:

```bash
python manage.py collectstatic --noinput
sudo systemctl reload nginx
```

Confirmar que Nginx apunta a:

```text
/home/smri/apps/system-industry-tesis/staticfiles/
```

### Error de ALLOWED_HOSTS

Agregar dominio o IP en `.env`:

```env
DJANGO_ALLOWED_HOSTS=TU_DOMINIO,TU_IP_DEL_SERVIDOR,localhost,127.0.0.1
```

Reiniciar:

```bash
sudo systemctl restart smri
```

### No conecta a la camara RTSP

Probar con:

```bash
ffprobe "rtsp://usuario:password@IP_CAMARA:554/stream1"
```

Revisar:

- La VPS debe tener ruta de red hacia la camara.
- Usuario y clave correctos.
- Puerto RTSP abierto solo en red privada/VPN.
- Stream correcto: `stream1`, `stream2`, `h264Preview_01_main`, etc.

### Instalacion lenta o fallida de `dlib`

Revisar RAM/swap y dependencias:

```bash
free -h
sudo apt install -y build-essential cmake libopenblas-dev liblapack-dev libboost-all-dev
```

## 22. Checklist final

- [ ] Dominio apuntando a la VPS.
- [ ] `.env` creado y protegido con `chmod 600`.
- [ ] `DEBUG=False`.
- [ ] `SECRET_KEY` segura y fuera del codigo.
- [ ] `ALLOWED_HOSTS` configurado.
- [ ] PostgreSQL con usuario y password propios.
- [ ] Migraciones ejecutadas.
- [ ] `collectstatic` ejecutado.
- [ ] Servicio `smri` activo en systemd.
- [ ] Nginx activo y apuntando al socket.
- [ ] HTTPS activo con Certbot.
- [ ] Firewall activo.
- [ ] Camaras RTSP accesibles desde la VPS por red segura.
- [ ] Backups definidos para base de datos y `media/`.

