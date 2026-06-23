import re
import uuid

from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import close_old_connections, transaction
from django.utils import timezone

from core_apps.camera.models import SecurityEvent
from core_apps.camera.services.incident_email import notify_incident_by_email
from core_apps.informes.models import Informe

DEFAULT_EVENT_SEVERITIES = {
    "face_recognized": "BAJO",
    "face_unknown": "MEDIO",
    "ppe_missing": "ALTO",
    "intrusion": "ALTO",
    "authorized_object": "BAJO",
    "unauthorized_object": "MEDIO",
    "dangerous_object": "ALTO",
    "fall_detected": "CRITICO",
    "phone_usage": "MEDIO",
    "collision_risk": "ALTO",
    "cut_risk": "CRITICO",
    "unauthorized_access": "ALTO",
}

EVENT_REASON_LABELS = {
    "face_recognized": "Rostro reconocido",
    "face_unknown": "Acceso no autorizado",
    "ppe_missing": "Falta EPP",
    "intrusion": "Intrusion",
    "authorized_object": "Objeto autorizado",
    "unauthorized_object": "Objeto no autorizado",
    "dangerous_object": "Objeto peligroso detectado",
    "fall_detected": "Movimiento",
    "phone_usage": "Uso de celular detectado",
    "collision_risk": "Riesgo de choque",
    "cut_risk": "Objeto peligroso detectado",
    "unauthorized_access": "Acceso no autorizado",
}

FIELD_MARKER_RE = re.compile(
    r"(?:^|\s*\|\s*)"
    r"(Motivo|Descripcion|Descripción|Categoria|Categoría|Nivel|Confianza|Persona)"
    r"\s*:\s*",
    re.IGNORECASE,
)


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


def _plain_person_name(user=None, authorized_person=None):
    if authorized_person is not None:
        return authorized_person.get_full_name() or "Persona autorizada"

    if user is not None:
        return user.get_full_name().strip() or user.username

    return "Desconocido/a"


def _extract_detail_field(text, field):
    pattern = re.compile(
        rf"(?:^|\s*\|\s*|\s+)(?:{field})\s*:\s*(.*?)(?=\s*(?:\|\s*)?"
        r"(?:Motivo|Descripcion|Descripción|Categoria|Categoría|Nivel|Confianza|Persona)\s*:|$)",
        re.IGNORECASE | re.DOTALL,
    )
    match = pattern.search(text or "")

    return match.group(1).strip() if match else ""


def _remove_detail_metadata(text):
    cleaned = FIELD_MARKER_RE.sub(" | ", text or "")
    cleaned = re.sub(
        r"\b(?:Categoria|Categoría|Nivel|Confianza|Persona)\s*:\s*[^|]+",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\s*\|\s*", " ", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)

    return cleaned.strip(" .|")


def _professionalize_description(reason, description, confidence=None):
    text = _remove_detail_metadata(description)

    if reason:
        text = re.sub(
            rf"^\s*{re.escape(reason)}\s*:\s*",
            "",
            text,
            flags=re.IGNORECASE,
        )

    replacements = {
        "gato en el area monitoreada": "Se detecto un gato en el area monitoreada",
        "animal en el area monitoreada": "Se detecto un animal en el area monitoreada",
        "perro en el area monitoreada": "Se detecto un perro en el area monitoreada",
        "ave en el area monitoreada": "Se detecto un ave en el area monitoreada",
        "celular detectado en el area monitoreada": "Se detecto un celular en el area monitoreada",
    }
    lower_text = text.lower()

    for source, replacement in replacements.items():
        if source in lower_text:
            text = replacement
            break

    if text:
        text = text[0].upper() + text[1:]
    else:
        text = f"Se registro el evento: {reason}."

    if not text.endswith("."):
        text += "."

    if confidence:
        confidence_match = re.search(r"\d+(?:\.\d+)?", confidence)
        confidence_value = confidence_match.group(0) if confidence_match else confidence
        text += f" Confianza: {confidence_value}."

    return text


def format_security_event_details(event_type, details, user=None, authorized_person=None):
    text = str(details or "").strip()

    event_labels = dict(SecurityEvent.EVENT_TYPES)
    category = event_labels.get(event_type, event_type)
    reason = _extract_detail_field(text, "Motivo") or EVENT_REASON_LABELS.get(event_type, category)
    description = _extract_detail_field(text, "Descripcion|Descripción") or text
    confidence = _extract_detail_field(text, "Confianza")

    if event_type == "ppe_missing" and "Indumentaria incorrecta" in description:
        reason = "Indumentaria incorrecta"

    person = _plain_person_name(user=user, authorized_person=authorized_person)
    parsed_person = _extract_detail_field(text, "Persona")

    if person == "Desconocido/a" and parsed_person:
        person = parsed_person

    if person.strip().lower() in {"persona no identificada", "no identificado", "desconocido"}:
        person = "Desconocido/a"
    formatted_description = _professionalize_description(
        reason,
        description,
        confidence=confidence,
    )

    return (
        f"Motivo: {reason}\n"
        f"Descripcion: {formatted_description}\n"
        f"Categoria: {category}\n"
        f"Persona: {person}"
    )


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

        formatted_details = format_security_event_details(
            event_type,
            details,
            user=user,
            authorized_person=authorized_person,
        )

        with transaction.atomic():
            event = SecurityEvent.objects.create(
                event_type=event_type,
                severity=normalize_event_severity(severity, event_type),
                details=formatted_details,
                image_path=image_path,
                related_user=user,
                authorized_person=authorized_person,
                camera=camera
            )

            persona = _plain_person_name(user=user, authorized_person=authorized_person)

            if epp_correcto is None:
                epp_correcto = False

            Informe.objects.create(
                security_event=event,
                camara=camera_name,
                persona_detectada=persona,
                epp_correcto=epp_correcto,
                descripcion=f"{event.get_event_type_display()}: {formatted_details}",
                evidencia=image_path
            )
            transaction.on_commit(lambda: notify_incident_by_email(event.pk))

        return event

    except Exception as e:
        print(f"[ERROR] No se pudo crear evento/informe: {e}")
        return None
