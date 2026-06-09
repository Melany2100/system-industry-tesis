import re
import uuid

from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import close_old_connections
from django.utils import timezone

from core_apps.camera.models import SecurityEvent
from core_apps.informes.models import Informe

DEFAULT_EVENT_SEVERITIES = {
    "face_recognized": "BAJO",
    "face_unknown": "MEDIO",
    "ppe_missing": "ALTO",
    "intrusion": "ALTO",
    "authorized_object": "BAJO",
    "unauthorized_object": "MEDIO",
    "dangerous_object": "ALTO",
    "unauthorized_access": "ALTO",
}


try:
    import cv2  # type: ignore
except Exception:
    cv2 = None


def build_event_image_path(event_type):
    now = timezone.localtime()
    unique_id = uuid.uuid4().hex[:8]

    filename = f"{event_type}_{now.strftime('%Y%m%d_%H%M%S')}_{unique_id}.jpg"

    return (
        f"security_events/"
        f"{now.year}/"
        f"{now.month:02d}/"
        f"{now.day:02d}/"
        f"{filename}"
    )


def save_event_image(frame, event_type, jpeg_quality=85):
    try:
        if cv2 is None:
            print("[ERROR] OpenCV no está instalado.")
            return None

        if frame is None:
            print("[ERROR] No se recibió frame para guardar evidencia.")
            return None

        image_path = build_event_image_path(event_type)

        success, buffer = cv2.imencode(
            ".jpg",
            frame,
            [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality]
        )

        if not success:
            print("[ERROR] No se pudo convertir el frame a JPG.")
            return None

        image_file = ContentFile(buffer.tobytes())
        saved_path = default_storage.save(image_path, image_file)

        return saved_path

    except Exception as e:
        print(f"[ERROR] Error al guardar imagen de evento: {e}")
        return None


def build_authorized_face_image_path(correo):
    now = timezone.localtime()
    unique_id = uuid.uuid4().hex[:8]

    safe_email = re.sub(r'[^a-zA-Z0-9_-]', '_', correo.split("@")[0])

    filename = f"{safe_email}_{now.strftime('%Y%m%d_%H%M%S')}_{unique_id}.jpg"

    return (
        f"authorized_faces/"
        f"{now.year}/"
        f"{now.month:02d}/"
        f"{filename}"
    )


def save_authorized_face_image(image_file, correo):
    try:
        if image_file is None:
            return None

        image_path = build_authorized_face_image_path(correo)
        saved_path = default_storage.save(image_path, image_file)

        return saved_path

    except Exception as e:
        print(f"[ERROR] Error al guardar imagen facial autorizada: {e}")
        return None


def can_save_event(event_key, seconds=20):
    if cache.get(event_key):
        return False

    cache.set(event_key, True, timeout=seconds)
    return True


def normalize_event_severity(value, event_type=None, default="MEDIO"):
    if not value:
        return DEFAULT_EVENT_SEVERITIES.get(event_type, default)

    level = str(value).strip().upper().replace("Í", "I")
    aliases = {
        "BAJA": "BAJO",
        "MEDIA": "MEDIO",
        "ALTA": "ALTO",
    }
    level = aliases.get(level, level)

    valid_levels = {choice[0] for choice in SecurityEvent.SEVERITY_LEVELS}

    if level in valid_levels:
        return level

    return DEFAULT_EVENT_SEVERITIES.get(event_type, default)


def create_security_event(
    event_type,
    details,
    frame=None,
    user=None,
    camara=None,
    camera=None,
    authorized_person=None,
    epp_correcto=None,
    severity=None
):
    try:
        close_old_connections()
        image_path = None

        if frame is not None:
            image_path = save_event_image(frame, event_type)

        if camera is not None:
            camera_name = camera.nombre
        elif camara:
            camera_name = camara
        else:
            camera_name = "Cámara no especificada"

        event = SecurityEvent.objects.create(
            event_type=event_type,
            severity=normalize_event_severity(severity, event_type),
            details=details,
            image_path=image_path,
            related_user=user,
            authorized_person=authorized_person,
            camera=camera
        )

        if authorized_person is not None:
            persona = authorized_person.get_full_name()
        elif user:
            persona = user.get_full_name().strip() or user.username
        else:
            persona = "Desconocido"

        if epp_correcto is None:
            epp_correcto = False

        Informe.objects.create(
            security_event=event,
            camara=camera_name,
            persona_detectada=persona,
            epp_correcto=epp_correcto,
            descripcion=f"{event.get_event_type_display()}: {details}",
            evidencia=image_path
        )

        return event

    except Exception as e:
        print(f"[ERROR] No se pudo crear evento/informe: {e}")
        return None
