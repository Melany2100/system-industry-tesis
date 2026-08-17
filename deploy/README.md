# Despliegue de SMRI en DigitalOcean sin Docker

Esta preparación separa dos responsabilidades:

- **DigitalOcean:** Django, PostgreSQL, Nginx y MediaMTX. No instala OpenCV,
  Ultralytics ni modelos de IA.
- **Máquina local:** abre las cámaras, ejecuta la detección, publica el video
  procesado hacia MediaMTX y replica los eventos por una API HTTPS autenticada.

Los eventos se guardan primero en PostgreSQL local. Una cola durable los envía
a DigitalOcean y reintenta con espera exponencial si la red se interrumpe.

## 1. Crear el Droplet y preparar DNS

Ubuntu 24.04, 2 vCPU y 2 GB RAM son suficientes para una instalación inicial
con uno o dos streams porque MediaMTX no ejecuta inferencia. 4 GB dan más margen
si PostgreSQL también vive en el Droplet.

Crea un registro DNS `A` para `app.midominio.com` apuntando a la IP pública.
En el Firewall de DigitalOcean permite:

- TCP 22 únicamente desde tu IP de administración.
- TCP 80 y 443 desde Internet.
- UDP 8189 desde Internet (tráfico WebRTC).
- TCP 8554 únicamente desde la IP pública de la máquina local que publica.

No abras 5432, 8889 ni 9997 a Internet.

## 2. Instalar paquetes del servidor

```bash
sudo apt update
sudo apt install -y python3-venv python3-dev build-essential libpq-dev \
  postgresql postgresql-contrib nginx certbot git curl
sudo adduser --system --group --home /opt/smri smri
sudo usermod -aG www-data smri
```

Clona o copia el repositorio en `/opt/smri` y deja los archivos a nombre de
`smri:www-data`:

```bash
sudo chown -R smri:www-data /opt/smri
sudo -u smri python3 -m venv /opt/smri/.venv
sudo -u smri /opt/smri/.venv/bin/pip install --upgrade pip
sudo -u smri /opt/smri/.venv/bin/pip install -r /opt/smri/requirements-web.txt
```

## 3. Crear PostgreSQL

```bash
sudo -u postgres psql
```

Dentro de `psql`:

```sql
CREATE USER smri_user WITH PASSWORD 'CAMBIAR_PASSWORD_POSTGRES';
CREATE DATABASE smri_prod OWNER smri_user;
\q
```

## 4. Configurar Django

```bash
sudo -u smri cp /opt/smri/.env.vps.example /opt/smri/.env
sudo chmod 640 /opt/smri/.env
sudo nano /opt/smri/.env
```

Reemplaza el dominio, la IP, las contraseñas y `SECRET_KEY`. Genera esta última
con:

```bash
/opt/smri/.venv/bin/python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

Genera también el token de sincronización y colócalo como
`SMRI_EVENT_SYNC_TOKEN` en el `.env` de la VPS y de la máquina local:

```bash
openssl rand -hex 32
```

Luego prepara Django:

```bash
cd /opt/smri
sudo -u smri /opt/smri/.venv/bin/python manage.py migrate
sudo -u smri /opt/smri/.venv/bin/python manage.py collectstatic --noinput
sudo -u smri /opt/smri/.venv/bin/python manage.py createsuperuser
sudo mkdir -p /opt/smri/media /opt/smri/logs
sudo chown -R smri:www-data /opt/smri/media /opt/smri/logs /opt/smri/staticfiles
```

Instala el servicio web:

```bash
sudo cp /opt/smri/deploy/smri-web.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now smri-web
sudo systemctl status smri-web
```

## 5. Instalar MediaMTX sin Docker

La versión comprobada para esta plantilla es `1.19.3` (Linux amd64):

```bash
cd /tmp
curl -LO https://github.com/bluenviron/mediamtx/releases/download/v1.19.3/mediamtx_v1.19.3_linux_amd64.tar.gz
tar -xzf mediamtx_v1.19.3_linux_amd64.tar.gz
sudo install -m 0755 mediamtx /usr/local/bin/mediamtx
sudo adduser --system --group --no-create-home mediamtx
sudo mkdir -p /usr/local/etc
sudo cp /opt/smri/deploy/mediamtx.yml /usr/local/etc/mediamtx.yml
sudo chown root:mediamtx /usr/local/etc/mediamtx.yml
sudo chmod 640 /usr/local/etc/mediamtx.yml
sudo nano /usr/local/etc/mediamtx.yml
```

En `mediamtx.yml`, cambia dominio, IP pública y contraseña de publicación. La
misma contraseña se usará en la máquina local.

```bash
sudo cp /opt/smri/deploy/mediamtx.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now mediamtx
sudo systemctl status mediamtx
```

## 6. Activar HTTPS y Nginx

Detén Nginx temporalmente y solicita el certificado (DNS ya debe resolver):

```bash
sudo systemctl stop nginx
sudo certbot certonly --standalone -d app.midominio.com
sudo cp /opt/smri/deploy/smri.nginx.conf /etc/nginx/sites-available/smri
sudo ln -s /etc/nginx/sites-available/smri /etc/nginx/sites-enabled/smri
sudo rm /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl enable --now nginx
```

Antes de copiar la plantilla, reemplaza `app.midominio.com` en los archivos
`.env`, `mediamtx.yml` y `smri.nginx.conf` por tu dominio real.

Nginx expone MediaMTX en `https://app.midominio.com/stream/` y valida la sesión
del administrador contra Django. Los puertos HTTP internos de MediaMTX quedan
cerrados al exterior.

## 7. Preparar la máquina local para publicar video

Instala FFmpeg y confirma que esté disponible:

```powershell
ffmpeg -version
```

Copia `.env.local-vps.example` como `.env`, conserva `SMRI_NODE_ROLE=edge` y
configura:

```dotenv
MEDIAMTX_PUBLISH_BASE_URL=rtsp://publisher:TU_PASSWORD@IP_PUBLICA_DROPLET:8554
FFMPEG_BINARY=ffmpeg
SMRI_EVENT_SYNC_URL=https://app.midominio.com/camera/api/v1/events/
SMRI_EVENT_SYNC_TOKEN=EL_MISMO_TOKEN_DE_LA_VPS
SMRI_EDGE_NODE_ID=planta-principal
```

Ese archivo usa PostgreSQL local. No sustituyas `DB_HOST` por la IP del Droplet:
los eventos se replican por HTTPS y el puerto 5432 debe seguir cerrado.

Registra también las cámaras en Django cloud y usa el mismo `stream_path` en
ambos nodos. Si se deja vacío, se publica automáticamente como `camera-1`,
`camera-2`, etc.; en ese caso los IDs deben coincidir. Al iniciar
el worker local, el fotograma procesado se entrega a FFmpeg en un hilo separado
con una cola de un solo fotograma: una red lenta descarta video atrasado en vez
de congelar la detección.

En cloud el campo obligatorio `source` no abre ningún dispositivo; puede usarse
un identificador descriptivo como `push://camera-1`. En local debe conservar la
fuente real (`0` para webcam o la URL RTSP).

## 8. Comprobaciones

```bash
sudo systemctl status smri-web mediamtx nginx
sudo journalctl -u smri-web -n 100 --no-pager
sudo journalctl -u mediamtx -n 100 --no-pager
curl http://127.0.0.1:9997/v3/paths/list
```

Después de iniciar la publicación local, la API de MediaMTX debe mostrar la ruta
`camera-ID` o el `stream_path` elegido. Al entrar como administrador en Django,
el apartado Cámaras reproducirá esa ruta por WebRTC.

Para comprobar o incorporar eventos locales anteriores, ejecuta en la máquina
edge (por ejemplo, los 20 más recientes):

```powershell
python manage.py sync_security_events --backfill 20 --limit 20
```

El resultado debe indicar `Sincronizados: 20` o la cantidad disponible. Si un
envío falla, consulta en el admin local **Eventos pendientes de sincronización**;
el campo `last_error` conserva el motivo y el proceso vuelve a intentarlo sin
bloquear la cámara.
