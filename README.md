# security_system_industrial

Proyecto Django (Django 4.2) para un sistema de seguridad industrial.

## Módulos listos para usar

1) **Autenticación**
- Login: `/login/`
- Registro: `/register/`

2) **Informes (EPP)**
- Listado: `/informes/`

3) **Eventos de seguridad (API)**
- Listado JSON: `/camera/security-events/`
- Marcar como resuelto (POST): `/camera/security-events/<id>/resolve/`

> El streaming de cámara y el reconocimiento facial son **opcionales** y requieren dependencias extra (ver abajo).

## Instalación rápida

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/Mac: source .venv/bin/activate

pip install -r requirements.txt
python manage.py migrate
python manage.py seed_demo
python manage.py runserver
```

Luego entra a:
- `http://127.0.0.1:8000/login/`
- Usuario demo: `admin`
- Clave demo: `admin12345`

## Dependencias opcionales para el módulo de cámara

Si quieres usar `/camera/video_feed/` y `/camera/register_face/`, instala:

```bash
pip install numpy opencv-python face_recognition
```

## Camaras VIGI / RTSP de alta resolucion

- Registra la URL `rtsp://USUARIO:CLAVE@IP:554/stream2` para equipos sin GPU
  dedicada. Si `stream2` ya es 1920x1080, el sistema lo usa tanto para la vista
  como para deteccion y evita abrir simultaneamente el perfil 2560x1440.
- Si se registra `stream1`, el sistema usa `stream2` para la vista y solo abre
  el flujo principal cuando el substream tiene menos de 1280 px de ancho.
- Las inferencias de objetos y EPP trabajan en segundo plano y conservan solo
  el frame mas reciente; una inferencia lenta no debe congelar el video.
- Los timeouts y la reconexion se controlan con
  `RTSP_OPEN_TIMEOUT_MILLISECONDS`, `RTSP_READ_TIMEOUT_MILLISECONDS` y
  `RTSP_RECONNECT_DELAY_SECONDS`.
- Las webcams USB mantienen su configuracion independiente mediante
  `LOCAL_CAMERA_CAPTURE_WIDTH`, `LOCAL_CAMERA_CAPTURE_HEIGHT` y
  `LOCAL_CAMERA_CAPTURE_FPS`.

Notas:
- `face_recognition` puede requerir dependencias nativas (dlib/cmake) según tu sistema operativo.
