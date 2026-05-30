import uuid
import cv2

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.utils import timezone
from django.core.cache import cache

from core_apps.camera.models import SecurityEvent


def build_evidence_path(event_type: str, extension: str = "jpg") -> str:
    """
    Construye una ruta organizada por año, mes y día.

    Ejemplo:
    security_events/2026/05/21/dangerous_object_20260521_221530_ab12cd34.jpg
    """

    now = timezone.localtime()
    unique_id = uuid.uuid4().hex[:8]

    filename = f"{event_type}_{now.strftime('%Y%m%d_%H%M%S')}_{unique_id}.{extension}"

    return (
        f"security_events/"
        f"{now.year}/"
        f"{now.month:02d}/"
        f"{now.day:02d}/"
        f"{filename}"
    )


def can_save_event(event_key: str, seconds: int = 20) -> bool:
    """
    Evita guardar muchas imágenes repetidas del mismo evento.
    Por ejemplo, si detecta un objeto peligroso durante varios segundos,
    solo guarda una evidencia cada 20 segundos.
    """

    if cache.get(event_key):
        return False

    cache.set(event_key, True, timeout=seconds)
    return True


def save_security_event_with_evidence(
    frame,
    event_type: str,
    details: str,
    related_user=None,
    jpeg_quality: int = 85
) -> SecurityEvent:
    """
    Guarda la imagen capturada en MEDIA_ROOT y registra el evento en la base.

    frame: frame de OpenCV.
    event_type: dangerous_object, unauthorized_access, face_unknown, etc.
    details: descripción del evento.
    related_user: usuario relacionado si aplica.
    """

    path = build_evidence_path(event_type)

    success, buffer = cv2.imencode(
        ".jpg",
        frame,
        [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality]
    )

    if not success:
        raise ValueError("No se pudo convertir el frame a imagen JPG.")

    image_file = ContentFile(buffer.tobytes())

    saved_path = default_storage.save(path, image_file)

    event = SecurityEvent.objects.create(
        event_type=event_type,
        details=details,
        image_path=saved_path,
        related_user=related_user,
        resolved=False
    )

    return event