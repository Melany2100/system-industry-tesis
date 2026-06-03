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

Notas:
- `face_recognition` puede requerir dependencias nativas (dlib/cmake) según tu sistema operativo.

## Generar una nueva clave secreta
```python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"