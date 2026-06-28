# Configuracion `.env` para que la maquina local envie eventos a la VPS

Este proyecto actualmente registra eventos mediante el ORM de Django, es decir,
con `SecurityEvent.objects.create(...)`. Por eso, para que la maquina local del
jefe procese las camaras VIGI C240 y los eventos aparezcan en la VPS, la opcion
compatible con el codigo actual es conectar la maquina local a la base PostgreSQL
de la VPS.

## Flujo recomendado

```text
Camaras VIGI C240 -> Switch -> Maquina local del jefe -> PostgreSQL VPS -> Sistema web VPS
```

La maquina local captura y procesa video. Cuando detecta un evento, lo guarda en
la base de datos de la VPS. El sistema web desplegado en la VPS muestra esos
eventos porque lee la misma base.

## 1. Usar la plantilla

Copiar el archivo de ejemplo:

```powershell
Copy-Item .env.local-vps.example .env
```

Editar `.env` y cambiar:

- `DJANGO_SECRET_KEY`
- `DB_PASSWORD`
- credenciales SMTP
- contrasenas RTSP de las camaras
- IP local de la PC si no es `192.168.10.10`
- IPs de camaras si no son `192.168.10.201` y `192.168.10.202`

## 2. Abrir tunel seguro hacia PostgreSQL de la VPS

En la maquina local, antes de arrancar el sistema:

```powershell
ssh -N -L 15432:127.0.0.1:5432 usuario_vps@IP_PUBLICA_VPS
```

Mientras esa ventana siga abierta, este `.env` hace que Django escriba en la
base de la VPS:

```env
DB_HOST=127.0.0.1
DB_PORT=15432
```

No es necesario abrir PostgreSQL al publico.

## 3. Registrar las camaras en la base de la VPS

Como la maquina local ahora apunta a la base de la VPS, se pueden registrar desde
el admin local o desde el admin web de la VPS.

Crear dos camaras:

```text
Nombre: VIGI C240 - Area 1
Source: rtsp://admin:CAMBIAR_PASSWORD_CAMARA@192.168.10.201:554/stream2
Ubicacion: Area 1
Activa: Si
```

```text
Nombre: VIGI C240 - Area 2
Source: rtsp://admin:CAMBIAR_PASSWORD_CAMARA@192.168.10.202:554/stream2
Ubicacion: Area 2
Activa: Si
```

## 4. Iniciar el sistema local

```powershell
.\start_smri.bat
```

O manualmente:

```powershell
$env:SMRI_AUTOSTART_CAMERAS="1"
$env:SMRI_CAMERA_AUTOSTART_FPS="8"
python manage.py runserver 0.0.0.0:8000
```

## 5. Verificar

En la maquina local:

```powershell
python manage.py check
```

Probar que se conecta a la base de la VPS:

```powershell
python manage.py shell
```

```python
from core_apps.camera.models import SecurityEvent
SecurityEvent.objects.count()
```

Luego abrir:

```text
http://127.0.0.1:8000/camera/
```

Los eventos creados deben aparecer tambien en la VPS:

```text
https://TU_DOMINIO_O_IP_VPS/alerta/
```

## Nota sobre evidencias

Con esta configuracion, los registros de eventos se guardan en la base de la VPS.
Sin embargo, las imagenes de evidencia se guardan en el `media/` de la maquina
que procesa el video. Para que las evidencias se vean desde la VPS, hay que
sincronizar `media/` hacia la VPS o configurar un almacenamiento compartido.

La solucion practica inicial es sincronizar:

```powershell
scp -r media usuario_vps@IP_PUBLICA_VPS:/home/smri/apps/system-industry-tesis/
```

Para produccion continua conviene automatizar esa sincronizacion o implementar
un endpoint/API que reciba evento + evidencia en la VPS.
