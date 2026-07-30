from core_apps import camera
from core_apps.camera.utils import cv2

import os
import cv2
import json
import re
import time
import traceback
from collections import deque
from datetime import datetime, timedelta
from io import BytesIO
from threading import Event, Lock, Thread

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.validators import validate_email
from django.db import IntegrityError, close_old_connections, transaction
from django.db.models import Count, Q
from django.http import JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView
from PIL import Image, ImageOps, UnidentifiedImageError

from .models import AuthorizedPerson, SecurityEvent, Camera
from core_apps.camera.utils import (
    can_save_event,
    create_security_event,
    format_security_event_details,
    save_authorized_face_image,
)
from core_apps.common.permissions import get_authorized_person_for_user, is_admin_user

# =========================
# LIVE LOG (RAM) - incremental
# =========================
_LIVE_LOG = deque(maxlen=300)
_LOG_LOCK = Lock()
_LOG_SEQ = 0
_LAST_LOG_TS: dict[str, float] = {}


def _display_user(user):
    if not user:
        return ""

    full_name = user.get_full_name().strip()
    return full_name or user.username


def _can_view_event_evidence(user, event, user_authorized_person=None):
    if not user or not user.is_authenticated:
        return False

    if is_admin_user(user):
        return True

    if user_authorized_person is None:
        user_authorized_person = get_authorized_person_for_user(user)

    return (
        user_authorized_person is not None
        and event.authorized_person_id == user_authorized_person.id
    )


def _json_forbidden(message="No tienes permisos para realizar esta accion."):
    return JsonResponse({"success": False, "message": message}, status=403)


class AdminRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    login_url = "/login/"
    raise_exception = True

    def test_func(self):
        return is_admin_user(self.request.user)


def _format_local_datetime(value):
    if value is None:
        return ""

    return timezone.localtime(value).strftime("%Y-%m-%d %H:%M:%S")


def _normalize_alert_level(value, default="MEDIO"):
    if not value:
        return default

    level = str(value).strip().upper().replace("Ã", "I")

    if level == "BAJA":
        return "BAJO"

    if level == "MEDIA":
        return "MEDIO"

    if level == "ALTA":
        return "ALTO"

    if level in ALERT_LEVELS:
        return level

    return default


def _alert_level_meta(level):
    normalized = _normalize_alert_level(level)
    meta = ALERT_LEVELS.get(normalized, ALERT_LEVELS["MEDIO"])

    return {
        "priority": normalized,
        "priority_label": normalized,
        "priority_class": meta["className"],
        "priority_icon": meta["icon"],
    }


def _extract_alert_level(details, event_type=None):
    match = ALERT_LEVEL_PATTERN.search(details or "")

    if match:
        return _normalize_alert_level(match.group(1))

    return DEFAULT_EVENT_LEVELS.get(event_type, "MEDIO")


def _get_event_level(event):
    severity = getattr(event, "severity", None)

    if severity:
        return _normalize_alert_level(severity)

    return _extract_alert_level(event.details, event.event_type)


def _event_detection_count(event, detection_counts=None):
    if detection_counts is not None:
        return detection_counts.get(event.authorized_person_id, 0)

    if not event.authorized_person_id:
        return SecurityEvent.objects.filter(authorized_person__isnull=True).count()

    return SecurityEvent.objects.filter(
        authorized_person_id=event.authorized_person_id
    ).count()


def _detection_counts_for_people(person_ids):
    include_unknown = any(person_id is None for person_id in person_ids)
    person_ids = [person_id for person_id in person_ids if person_id]
    counts = {}

    if include_unknown:
        counts[None] = SecurityEvent.objects.filter(
            authorized_person__isnull=True
        ).count()

    if not person_ids:
        return counts

    counts.update({
        item["authorized_person_id"]: item["total"]
        for item in (
            SecurityEvent.objects
            .filter(authorized_person_id__in=person_ids)
            .values("authorized_person_id")
            .annotate(total=Count("id"))
        )
    })

    return counts


def _event_payload(
    event,
    include_image_path=False,
    request_user=None,
    user_authorized_person=None,
    detection_counts=None,
):
    level = _get_event_level(event)
    can_manage = bool(request_user and is_admin_user(request_user))
    can_view_evidence = _can_view_event_evidence(
        request_user,
        event,
        user_authorized_person=user_authorized_person,
    )
    payload = {
        "id": event.id,
        "event_type": event.event_type,
        "event_type_display": event.get_event_type_display(),
        "details": format_security_event_details(
            event.event_type,
            event.details,
            user=event.related_user,
            authorized_person=event.authorized_person,
        ),
        "timestamp": _format_local_datetime(event.timestamp),
        "severity": level,
        "resolved": event.resolved,
        "identified_person": event.get_person_name(),
        "identified_person_id": event.authorized_person_id,
        "detection_count": _event_detection_count(event, detection_counts=detection_counts),
        "reviewed_by": _display_user(event.reviewed_by),
        "reviewed_at": _format_local_datetime(event.reviewed_at),
        "managed_by": _display_user(event.managed_by),
        "managed_at": _format_local_datetime(event.managed_at),
        "can_view_evidence": can_view_evidence,
        "can_manage": can_manage,
        "can_resolve": can_manage and not event.resolved,
        "email_status": event.email_status,
        "email_status_display": event.get_email_status_display(),
        "email_recipient": event.email_recipient,
        "email_cc": event.email_cc if can_manage else "",
        "email_sent_at": _format_local_datetime(event.email_sent_at),
        "email_error": event.email_error if can_manage else "",
        "can_retry_email": can_manage,
        **_alert_level_meta(level),
    }

    image_url = event.get_image_url() if can_view_evidence and hasattr(event, "get_image_url") else None

    if include_image_path:
        payload["image_path"] = image_url
    else:
        payload["image_url"] = image_url
        payload["location"] = event.camera.ubicacion if event.camera and event.camera.ubicacion else ""
        payload["camera"] = event.camera.nombre if event.camera else "Sin cÃ¡mara"
        payload["user"] = event.related_user.username if getattr(event, "related_user", None) else "Sistema"

    return payload


def _requested_alert_level(value):
    return _normalize_alert_level(value, default="") if value else ""


def _local_day_bounds(date_value):
    if not date_value:
        return None

    try:
        parsed_date = datetime.strptime(date_value, "%Y-%m-%d").date()
    except ValueError:
        return None

    current_timezone = timezone.get_current_timezone()
    start = timezone.make_aware(
        datetime.combine(parsed_date, datetime.min.time()),
        current_timezone,
    )

    return start, start + timedelta(days=1)


def _filtered_security_events_queryset(request):
    queryset = SecurityEvent.objects.select_related(
        "camera",
        "related_user",
        "authorized_person",
        "reviewed_by",
        "managed_by",
    ).all()
    user_authorized_person = get_authorized_person_for_user(request.user)

    if not is_admin_user(request.user):
        if user_authorized_person is not None:
            identity_filter = Q(authorized_person=user_authorized_person)

            for value in (
                user_authorized_person.get_full_name(),
                user_authorized_person.nombres,
                user_authorized_person.apellidos,
                user_authorized_person.correo,
            ):
                if value:
                    identity_filter |= Q(details__icontains=value)

            queryset = queryset.filter(identity_filter).exclude(authorized_person__isnull=True)
        else:
            identity_filter = Q(related_user=request.user)

            for value in (
                request.user.get_full_name(),
                request.user.first_name,
                request.user.last_name,
                request.user.username,
                request.user.email,
            ):
                value = (value or "").strip()

                if value:
                    identity_filter |= Q(details__icontains=value)

            queryset = queryset.filter(identity_filter)

    event_type = (request.GET.get("type") or request.GET.get("event_type") or "").strip()
    search = (request.GET.get("q") or "").strip()
    date_bounds = _local_day_bounds((request.GET.get("date") or "").strip())
    valid_event_types = {choice[0] for choice in SecurityEvent.EVENT_TYPES}

    if event_type in valid_event_types:
        queryset = queryset.filter(event_type=event_type)

    if date_bounds:
        start, end = date_bounds
        queryset = queryset.filter(timestamp__gte=start, timestamp__lt=end)

    if search:
        matching_display_types = [
            value
            for value, label in SecurityEvent.EVENT_TYPES
            if search.lower() in label.lower()
        ]
        search_filter = (
            Q(event_type__icontains=search)
            | Q(details__icontains=search)
            | Q(related_user__username__icontains=search)
            | Q(camera__nombre__icontains=search)
            | Q(authorized_person__nombres__icontains=search)
            | Q(authorized_person__apellidos__icontains=search)
            | Q(authorized_person__correo__icontains=search)
            | Q(reviewed_by__username__icontains=search)
            | Q(managed_by__username__icontains=search)
        )

        if matching_display_types:
            search_filter |= Q(event_type__in=matching_display_types)

        queryset = queryset.filter(
            search_filter
        )

    return queryset.order_by("-timestamp")



# =========================
# Helpers de identidad y asociaciÃ³n rostro/persona
# =========================
FACE_MEMORY_SECONDS = 12.0
FACE_MATCH_DISTANCE = 220.0
FACE_OVERLAP_THRESHOLD = 0.20

PPE_INFERENCE_IMGSZ = getattr(settings, "PPE_INFERENCE_IMGSZ", 960)
PPE_MODEL_CONFIDENCE = getattr(settings, "PPE_MODEL_CONFIDENCE", 0.25)
PPE_CONFIRMATION_FRAMES = max(
    1,
    int(getattr(settings, "PPE_CONFIRMATION_FRAMES", 2)),
)
PPE_ITEM_OVERLAP_THRESHOLD = 0.10
PPE_VIOLATION_TTL_SECONDS = 6.0
# Elementos exigidos por el modelo SH17 integrado en camera/ppe.pt.
PPE_REQUIRED_ITEMS = (
    "mask",
    "gloves",
    "earmuffs",
    "hardhat",
    "safety glasses",
)
PPE_ITEM_DISPLAY_NAMES = {
    "mask": "mascarilla",
    "gloves": "guantes",
    "earmuffs": "protectores auditivos",
    "hardhat": "casco",
    "safety glasses": "gafas de proteccion",
}
PPE_CLASS_CONFIDENCE = {
    "person": getattr(settings, "PPE_PERSON_CONFIDENCE", 0.45),
    "hardhat": getattr(settings, "PPE_HARDHAT_CONFIDENCE", 0.35),
    "mask": getattr(settings, "PPE_MASK_CONFIDENCE", 0.40),
    "gloves": getattr(settings, "PPE_GLOVES_CONFIDENCE", 0.30),
    "earmuffs": getattr(settings, "PPE_EARMUFFS_CONFIDENCE", 0.20),
    "safety glasses": getattr(
        settings,
        "PPE_SAFETY_GLASSES_CONFIDENCE",
        0.25,
    ),
    "safety vest": 0.45,
    "no-hardhat": 0.65,
    "no-mask": getattr(settings, "PPE_NO_MASK_CONFIDENCE", 0.65),
    "no-gloves": 0.65,
    "no-earmuffs": 0.65,
    "no-safety vest": 0.65,
    "safety cone": 0.50,
    "machinery": 0.50,
    "vehicle": 0.50,
}
ALERT_LEVELS = {
    "BAJO": {
        "className": "success",
        "icon": "fa-circle-check",
    },
    "MEDIO": {
        "className": "warning",
        "icon": "fa-circle-exclamation",
    },
    "ALTO": {
        "className": "danger",
        "icon": "fa-triangle-exclamation",
    },
    "CRITICO": {
        "className": "critical",
        "icon": "fa-radiation",
    },
}
DEFAULT_EVENT_LEVELS = {
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
ALERT_LEVEL_PATTERN = re.compile(
    r"(?:nivel|prioridad)\s*:\s*(BAJO|BAJA|MEDIO|MEDIA|ALTO|ALTA|CRITICO|CRÃTICO)",
    re.IGNORECASE,
)


def _safe_event_key(value: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in str(value).lower())


def _box_area(box) -> int:
    x1, y1, x2, y2 = box
    return max(0, x2 - x1) * max(0, y2 - y1)


def _intersection_area(box_a, box_b) -> int:
    ax1, ay1, ax2, ay2 = box_a
    bx1, by1, bx2, by2 = box_b

    ix1 = max(ax1, bx1)
    iy1 = max(ay1, by1)
    ix2 = min(ax2, bx2)
    iy2 = min(ay2, by2)

    return max(0, ix2 - ix1) * max(0, iy2 - iy1)


def _box_center(box):
    x1, y1, x2, y2 = box
    return (x1 + x2) // 2, (y1 + y2) // 2


def _normalize_ppe_label(label) -> str:
    normalized = str(label).strip().lower().replace("_", "-")
    aliases = {
        # Nombres publicados por el modelo SH17.
        "face-mask": "mask",
        "face-mask-medical": "mask",
        "helmet": "hardhat",
        "glasses": "safety glasses",
        "goggles": "safety glasses",
        "ear-mufs": "earmuffs",
        "ear-muffs": "earmuffs",
        # Variantes habituales de otros modelos PPE compatibles.
        "safety-glasses": "safety glasses",
        "no-helmet": "no-hardhat",
        "no-glasses": "no-safety glasses",
        "no-goggles": "no-safety glasses",
        "no-ear-mufs": "no-earmuffs",
        "no-ear-muffs": "no-earmuffs",
    }
    return aliases.get(normalized, normalized)


def _passes_ppe_confidence(label: str, confidence: float) -> bool:
    min_confidence = PPE_CLASS_CONFIDENCE.get(label, 0.55)
    return confidence >= min_confidence


def _is_valid_ppe_person(frame_shape, person_box, confidence: float) -> bool:
    """Descarta falsas personas antes de evaluar si les falta EPP."""
    if not _passes_ppe_confidence("person", confidence):
        return False

    frame_height, frame_width = frame_shape[:2]
    if frame_height <= 0 or frame_width <= 0:
        return False

    x1, y1, x2, y2 = person_box
    width = max(0, x2 - x1)
    height = max(0, y2 - y1)
    frame_area = float(frame_width * frame_height)

    if width <= 0 or height <= 0:
        return False

    area_ratio = (width * height) / frame_area
    height_ratio = height / float(frame_height)
    aspect_ratio = height / float(width)

    return (
        area_ratio >= getattr(settings, "PPE_PERSON_MIN_AREA_RATIO", 0.06)
        and height_ratio >= getattr(settings, "PPE_PERSON_MIN_HEIGHT_RATIO", 0.30)
        and aspect_ratio >= getattr(settings, "PPE_PERSON_MIN_ASPECT_RATIO", 0.75)
    )


def _is_ppe_person_corroborated(
    person_box,
    vision_event_detector,
    detected_faces,
    now,
) -> bool:
    """Exige otra evidencia humana existente antes de generar falta de EPP."""
    for face in detected_faces or []:
        face_box = face.get("coords")
        last_seen = face.get("last_seen", 0.0)

        if not face_box or now - last_seen > FACE_MEMORY_SECONDS:
            continue

        face_cx, face_cy = _box_center(face_box)
        px1, py1, px2, py2 = person_box
        if px1 <= face_cx <= px2 and py1 <= face_cy <= py2:
            return True

    if vision_event_detector is None:
        return False

    boxes_at = float(getattr(vision_event_detector, "last_person_boxes_at", 0.0) or 0.0)
    ttl = getattr(settings, "PPE_PERSON_CORROBORATION_TTL_SECONDS", 3.0)
    if not boxes_at or now - boxes_at > ttl:
        return False

    min_confidence = getattr(
        settings,
        "PPE_PERSON_CORROBORATION_CONFIDENCE",
        0.55,
    )
    person_area = _box_area(person_box)

    for candidate in getattr(vision_event_detector, "last_person_boxes", []) or []:
        candidate_box = tuple(candidate[:4])
        candidate_confidence = float(candidate[4]) if len(candidate) > 4 else 0.0

        if candidate_confidence < min_confidence:
            continue

        candidate_area = _box_area(candidate_box)
        smaller_area = min(person_area, candidate_area)
        if smaller_area <= 0:
            continue

        overlap_ratio = _intersection_area(person_box, candidate_box) / float(smaller_area)
        candidate_cx, candidate_cy = _box_center(candidate_box)
        px1, py1, px2, py2 = person_box

        if overlap_ratio >= 0.30 or (
            px1 <= candidate_cx <= px2 and py1 <= candidate_cy <= py2
        ):
            return True

    return False


def _get_supported_required_ppe_items(names) -> tuple[str, ...]:
    model_labels = {
        _normalize_ppe_label(label)
        for label in (names.values() if hasattr(names, "values") else names)
    }

    return tuple(
        item for item in PPE_REQUIRED_ITEMS
        if item in model_labels or f"no-{item}" in model_labels
    )


def _get_missing_ppe_items(present_items, required_items) -> tuple[str, ...]:
    """Devuelve, en orden, únicamente el EPP ausente de una persona."""
    present = {
        _normalize_ppe_label(item)
        for item in (present_items or ())
        if not _normalize_ppe_label(item).startswith("no-")
    }
    return tuple(item for item in required_items if item not in present)


def _is_ppe_item_inside_person(person_box, item_box) -> bool:
    item_area = _box_area(item_box)

    if item_area <= 0:
        return False

    overlap_ratio = _intersection_area(person_box, item_box) / float(item_area)

    if overlap_ratio >= PPE_ITEM_OVERLAP_THRESHOLD:
        return True

    item_cx, item_cy = _box_center(item_box)
    px1, py1, px2, py2 = person_box

    return px1 <= item_cx <= px2 and py1 <= item_cy <= py2


def _ppe_violation_key(camera_id, person_box, violation_type, base_msg):
    cx, cy = _box_center(person_box)
    grid_x = cx // 120
    grid_y = cy // 120

    return (
        f"{camera_id}:{grid_x}:{grid_y}:"
        f"{violation_type}:{_safe_event_key(base_msg)}"
    )


def _track_ppe_violation(memory, key, now):
    entry = memory.get(key, {"count": 0})
    entry["count"] += 1
    entry["last_seen"] = now
    memory[key] = entry

    return entry["count"] >= PPE_CONFIRMATION_FRAMES, entry["count"]


def _prune_ppe_violations(memory, observed_keys, now):
    for key in list(memory.keys()):
        last_seen = memory[key].get("last_seen", 0.0)

        if key not in observed_keys or now - last_seen > PPE_VIOLATION_TTL_SECONDS:
            del memory[key]


def _match_identity_to_person_box(person_box, detected_faces):
    """
    Relaciona la persona detectada por PPE con un rostro reconocido.

    Primero intenta por solapamiento.
    Luego intenta por centro del rostro.
    """
    px1, py1, px2, py2 = person_box

    best_face = None
    best_score = 0.0

    for face in detected_faces:
        face_box = face.get("coords")

        if not face_box:
            continue

        face_area = _box_area(face_box)

        if face_area <= 0:
            continue

        inter_area = _intersection_area(person_box, face_box)
        overlap_ratio = inter_area / float(face_area)

        if overlap_ratio >= FACE_OVERLAP_THRESHOLD and overlap_ratio > best_score:
            best_score = overlap_ratio
            best_face = face

    if best_face is not None:
        return best_face

    # Fallback: centro del rostro dentro de la caja de persona
    for face in detected_faces:
        face_box = face.get("coords")

        if not face_box:
            continue

        face_cx, face_cy = _box_center(face_box)

        if px1 <= face_cx <= px2 and py1 <= face_cy <= py2:
            return face

    return None


def _merge_recent_faces(old_faces, new_faces, now):
    """
    Mantiene en memoria rostros recientes, aunque face_recognition
    no los vea en un frame especÃ­fico.
    """
    merged_faces = list(new_faces)

    for old_face in old_faces:
        last_seen = old_face.get("last_seen", 0.0)

        if now - last_seen > FACE_MEMORY_SECONDS:
            continue

        old_center = old_face.get("center")

        if not old_center:
            continue

        already_matched = False

        for new_face in new_faces:
            new_center = new_face.get("center")

            if not new_center:
                continue

            dx = new_center[0] - old_center[0]
            dy = new_center[1] - old_center[1]
            dist = (dx ** 2 + dy ** 2) ** 0.5

            if dist < FACE_MATCH_DISTANCE:
                already_matched = True
                break

        if not already_matched:
            merged_faces.append(old_face)

    return merged_faces


def _get_recent_identity(detected_faces, now):
    """
    Fallback para cuando PPE no logra asociar por caja.
    Si hay una persona en pantalla y recientemente se reconociÃ³ un rostro,
    usa esa identidad.
    """
    recent_faces = [
        face for face in detected_faces
        if now - face.get("last_seen", 0.0) <= FACE_MEMORY_SECONDS
    ]

    if not recent_faces:
        return None

    authorized_faces = [
        face for face in recent_faces
        if face.get("is_authorized")
    ]

    if authorized_faces:
        return max(
            authorized_faces,
            key=lambda face: face.get("last_seen", 0.0)
        )

    return max(
        recent_faces,
        key=lambda face: face.get("last_seen", 0.0)
    )


def _get_identity_text(identity):
    if identity is None:
        return "Persona no identificada", "No autorizado", None

    if identity.get("is_authorized"):
        return (
            identity.get("name", "Persona autorizada"),
            "Autorizado",
            identity.get("person_obj"),
        )

    return "Desconocido", "No autorizado", None


LIVE_LOG_TRANSLATIONS = {
    "FACE": "PERSONA",
    "PPE": "EPP",
    "OBJ": "OBJETO",
    "PHONE": "CELULAR",
    "bottle": "botella",
    "backpack": "mochila",
    "handbag": "bolso",
    "suitcase": "maleta",
    "cell_phone": "celular",
    "knife": "cuchillo",
    "scissors": "tijeras",
    "person": "persona",
    "mask": "mascarilla",
    "gloves": "guantes",
    "earmuffs": "protectores auditivos",
    "hardhat": "casco",
    "safety glasses": "gafas de proteccion",
    "cat": "gato",
    "dog": "perro",
    "bird": "ave",
}


def _clean_live_log_message(message: str) -> str:
    text = str(message or "")

    # Elimina emojis y secuencias mojibake que aparecian como âœ…, âŒ o âš .
    text = re.sub(r"(?:â[^\s]{0,3}|[✅❌⚠️🟢🟠🔴])\s*", "", text)

    for source, translated in LIVE_LOG_TRANSLATIONS.items():
        text = re.sub(
            rf"(?<![\w]){re.escape(source)}(?![\w])",
            translated,
            text,
            flags=re.IGNORECASE,
        )

    text = text.replace("model cargado", "modelo cargado")
    text = text.replace("Streaming iniciado", "Transmision iniciada")
    text = text.replace("Streaming detenido", "Transmision detenida")
    text = text.replace("No existe", "No se encontro")
    return " ".join(text.split())


def _live_log_kind(message: str) -> str:
    normalized = str(message or "").lower()

    if any(term in normalized for term in (
        "falta epp",
        "indumentaria incorrecta",
        "no autorizad",
        "objeto peligroso",
        "intrusion",
        "error",
        "no se pudo",
    )):
        return "danger"

    if any(term in normalized for term in (
        "epp ok",
        "epp correcto",
        "indumentaria correcta",
    )):
        return "success"

    if any(term in normalized for term in (
        "autorizado:",
        "persona identificada",
        "rostro reconocido",
    )):
        return "identity"

    return "info"

def _log_line(message: str, key: str | None = None, throttle_sec: float = 0.0) -> None:
    global _LOG_SEQ
    now = time.monotonic()

    if key and throttle_sec > 0:
        last = _LAST_LOG_TS.get(key, 0.0)
        if (now - last) < throttle_sec:
            return
        _LAST_LOG_TS[key] = now

    ts = timezone.localtime().strftime("%H:%M:%S")
    clean_message = _clean_live_log_message(message)
    kind = _live_log_kind(clean_message)

    with _LOG_LOCK:
        _LOG_SEQ += 1
        _LIVE_LOG.append({
            "id": _LOG_SEQ,
            "ts": ts,
            "msg": clean_message,
            "kind": kind,
        })


def live_status(request):
    """Devuelve logs nuevos usando ?after=<id>"""
    try:
        after = int(request.GET.get("after", "0"))
    except ValueError:
        after = 0

    with _LOG_LOCK:
        last_id = _LOG_SEQ
        lines = [x for x in _LIVE_LOG if x["id"] > after]
        lines = lines[-80:]

    return JsonResponse({"lines": lines, "last_id": last_id})


# =========================
# Safe imports
# =========================
def _safe_import_cv2():
    try:
        import cv2  # type: ignore
        return cv2
    except Exception:
        return None


def _safe_import_numpy():
    try:
        import numpy as np  # type: ignore
        return np
    except Exception:
        return None


def _safe_import_face_recognition():
    try:
        import face_recognition  # type: ignore
        return face_recognition
    except Exception:
        return None


def _safe_import_ultralytics():
    try:
        from ultralytics import YOLO  # type: ignore
        return YOLO
    except Exception:
        return None


_FACE_RECOGNITION_CACHE = {"module": None, "attempted": False}
_IMPORT_CACHE_LOCK = Lock()
_FACE_RECOGNITION_INFERENCE_LOCK = Lock()


def _load_face_recognition():
    with _IMPORT_CACHE_LOCK:
        if _FACE_RECOGNITION_CACHE["attempted"]:
            return _FACE_RECOGNITION_CACHE["module"]

        face_recognition = _safe_import_face_recognition()
        _FACE_RECOGNITION_CACHE["module"] = face_recognition
        _FACE_RECOGNITION_CACHE["attempted"] = True

    if face_recognition is None:
        _log_line("face_recognition no disponible: usando Haar Cascade", key="face_rec_missing", throttle_sec=10)
    else:
        _log_line("face_recognition cargado", key="face_rec_loaded", throttle_sec=10)

    return face_recognition


# =========================
# PPE (Ultralytics)
# =========================
_PPE_CACHE = {"model": None}
_PPE_LOAD_LOCK = Lock()
_PPE_INFERENCE_LOCK = Lock()
_RISK_YOLO_CACHE = {"detector": None}
_RISK_YOLO_LOAD_LOCK = Lock()
_VISION_EVENT_CACHE = {"detectors": {}}
_VISION_EVENT_LOAD_LOCK = Lock()


def _load_ppe_model():
    with _PPE_LOAD_LOCK:
        return _load_ppe_model_locked()


def _load_ppe_model_locked():
    if _PPE_CACHE["model"] is not None:
        return _PPE_CACHE["model"]

    YOLO = _safe_import_ultralytics()
    if YOLO is None:
        _log_line("Ultralytics no disponible: PPE deshabilitado", key="ultra_missing", throttle_sec=10)
        return None

    model_path = os.path.join(settings.BASE_DIR, "camera", "ppe.pt")
    if not os.path.exists(model_path):
        _log_line(f"âŒ No existe ppe.pt en: {model_path}", key="ppe_file_missing", throttle_sec=10)
        return None

    try:
        model = YOLO(model_path)
        _PPE_CACHE["model"] = model
        _log_line("âœ… PPE model cargado", key="ppe_loaded", throttle_sec=10)
        return model
    except Exception as e:
        _log_line(f"âŒ Error cargando PPE model: {e}", key="ppe_load_err", throttle_sec=10)
        return None


def _load_risk_yolo_detector():
    with _RISK_YOLO_LOAD_LOCK:
        if _RISK_YOLO_CACHE["detector"] is not None:
            return _RISK_YOLO_CACHE["detector"]

        try:
            from core_apps.camera.services.risk_yolo_detector import get_risk_yolo_detector

            detector = get_risk_yolo_detector()
            _RISK_YOLO_CACHE["detector"] = detector
            _log_line("YOLOv8s objetos de riesgo cargado", key="risk_yolo_loaded", throttle_sec=10)
            return detector
        except Exception as e:
            _log_line(f"Error cargando YOLOv8s objetos de riesgo: {e}", key="risk_yolo_load_err", throttle_sec=10)
            return None


def _load_vision_event_detector(key="preload"):
    with _VISION_EVENT_LOAD_LOCK:
        if key in _VISION_EVENT_CACHE["detectors"]:
            return _VISION_EVENT_CACHE["detectors"][key]

        try:
            from core_apps.camera.services.vision_event_detector import get_vision_event_detector

            detector = get_vision_event_detector(key=key)
            _VISION_EVENT_CACHE["detectors"][key] = detector
            _log_line("Detector visual general cargado", key="vision_event_loaded", throttle_sec=10)
            return detector
        except Exception as e:
            _log_line(f"Error cargando detector visual general: {e}", key="vision_event_load_err", throttle_sec=10)
            return None


_MODEL_PRELOAD_LOCK = Lock()
_MODEL_PRELOAD_DONE = Event()
_MODEL_PRELOAD_STARTED = False


def _preload_camera_models_task():
    try:
        _safe_import_cv2()
        _safe_import_numpy()
        _load_risk_yolo_detector()
        _load_vision_event_detector()
        _load_ppe_model()
        _load_face_recognition()
        _log_line("Modelos de camara precargados", key="models_preloaded", throttle_sec=10)
    finally:
        _MODEL_PRELOAD_DONE.set()


def preload_camera_models(async_load: bool = True):
    global _MODEL_PRELOAD_STARTED

    with _MODEL_PRELOAD_LOCK:
        if _MODEL_PRELOAD_STARTED:
            return

        _MODEL_PRELOAD_STARTED = True

    if async_load:
        Thread(
            target=_preload_camera_models_task,
            name="camera-model-preload",
            daemon=True,
        ).start()
    else:
        _preload_camera_models_task()


def _attach_preloaded_models():
    if not _MODEL_PRELOAD_DONE.is_set():
        return None, None, None

    return (
        _RISK_YOLO_CACHE["detector"],
        _PPE_CACHE["model"],
        _FACE_RECOGNITION_CACHE["module"],
    )


# =========================
# Frames (con FPS lento)
# =========================
class LatestFrameReader:
    def __init__(self, cap, camera_name):
        self.cap = cap
        self.camera_name = camera_name
        self._frame_lock = Lock()
        self._latest_frame = None
        self._latest_at = 0.0
        self._stopped = Event()
        self._thread = Thread(
            target=self._read_loop,
            name=f"latest-frame-{camera_name}",
            daemon=True,
        )

    def start(self):
        self._thread.start()

    def stop(self):
        self._stopped.set()
        self._thread.join(timeout=1.0)

    def get_latest(self, max_age_seconds=None):
        with self._frame_lock:
            if self._latest_frame is None:
                return False, None

            if max_age_seconds is not None:
                age = time.monotonic() - self._latest_at
                if age > max_age_seconds:
                    return False, None

            return True, self._latest_frame.copy()

    def _read_loop(self):
        while not self._stopped.is_set():
            ok, frame = self.cap.read()

            if not ok:
                time.sleep(0.05)
                continue

            with self._frame_lock:
                self._latest_frame = frame
                self._latest_at = time.monotonic()


def _bounded_camera_fps(value=None) -> int:
    min_fps = max(1, int(getattr(settings, "SYSTEM_TARGET_VIDEO_FPS", 8)))
    max_fps = max(min_fps, int(getattr(settings, "SYSTEM_MAX_INTERNAL_VIDEO_FPS", 12)))

    if value is None:
        return min_fps

    try:
        requested_fps = int(value)
    except (TypeError, ValueError):
        requested_fps = min_fps

    return max(min_fps, min(requested_fps, max_fps))


def _resize_frame_to_width(cv2_module, frame, target_width):
    if frame is None or not getattr(frame, "size", 0):
        return frame

    height, width = frame.shape[:2]
    target_width = max(1, int(target_width))
    if width <= target_width:
        return frame

    target_height = max(1, int(round(height * target_width / float(width))))
    return cv2_module.resize(
        frame,
        (target_width, target_height),
        interpolation=cv2_module.INTER_AREA,
    )


def _scale_box_between_frames(box, source_shape, target_shape):
    source_height, source_width = source_shape[:2]
    target_height, target_width = target_shape[:2]
    if source_width <= 0 or source_height <= 0:
        return tuple(map(int, box))

    scale_x = target_width / float(source_width)
    scale_y = target_height / float(source_height)
    x1, y1, x2, y2 = box
    return (
        int(round(x1 * scale_x)),
        int(round(y1 * scale_y)),
        int(round(x2 * scale_x)),
        int(round(y2 * scale_y)),
    )


def _rtsp_stream_sources(source):
    """Obtiene substream para vista y main stream para analisis en camaras VIGI."""
    source = str(source or "").strip()
    if (
        not source.lower().startswith("rtsp://")
        or not getattr(settings, "RTSP_DUAL_STREAM_ENABLED", True)
    ):
        return source, None

    main_name = str(getattr(settings, "RTSP_MAIN_STREAM_NAME", "stream1"))
    sub_name = str(getattr(settings, "RTSP_SUB_STREAM_NAME", "stream2"))
    stream_pattern = re.compile(
        rf"/(?:{re.escape(main_name)}|{re.escape(sub_name)})(?=$|\?)",
        flags=re.IGNORECASE,
    )
    if not stream_pattern.search(source):
        return source, None

    preview_source = stream_pattern.sub(f"/{sub_name}", source, count=1)
    analysis_source = stream_pattern.sub(f"/{main_name}", source, count=1)
    if preview_source == analysis_source:
        return preview_source, None
    return preview_source, analysis_source


def _open_rtsp_camera(cv2_module, source):
    cap = cv2_module.VideoCapture(source, cv2_module.CAP_FFMPEG)
    cap.set(cv2_module.CAP_PROP_BUFFERSIZE, 1)
    return cap


def _open_local_camera(cv2, camera_source: int, camera_name: str, target_fps: int):
    if os.name == "nt":
        backend_candidates = [
            ("DSHOW", getattr(cv2, "CAP_DSHOW", None)),
            ("MSMF", getattr(cv2, "CAP_MSMF", None)),
            ("DEFAULT", None),
        ]
    else:
        backend_candidates = [("DEFAULT", None)]

    for backend_name, backend in backend_candidates:
        if backend is None:
            cap = cv2.VideoCapture(camera_source)
        else:
            cap = cv2.VideoCapture(camera_source, backend)

        fourcc_name = str(getattr(settings, "LOCAL_CAMERA_FOURCC", "MJPG"))[:4]
        if len(fourcc_name) == 4:
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*fourcc_name))
        cap.set(
            cv2.CAP_PROP_FRAME_WIDTH,
            max(640, int(getattr(settings, "LOCAL_CAMERA_CAPTURE_WIDTH", 1920))),
        )
        cap.set(
            cv2.CAP_PROP_FRAME_HEIGHT,
            max(480, int(getattr(settings, "LOCAL_CAMERA_CAPTURE_HEIGHT", 1080))),
        )
        cap.set(
            cv2.CAP_PROP_FPS,
            max(
                int(target_fps),
                int(getattr(settings, "LOCAL_CAMERA_CAPTURE_FPS", 15)),
            ),
        )
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        if not cap.isOpened():
            cap.release()
            continue

        ok, frame = False, None
        warmup_deadline = time.monotonic() + 1.5
        while time.monotonic() < warmup_deadline:
            ok, frame = cap.read()
            if ok and frame is not None and getattr(frame, "size", 0):
                break
            time.sleep(0.03)

        if ok and frame is not None and getattr(frame, "size", 0):
            _log_line(
                f"Camara local abierta con backend {backend_name}: {camera_name}",
                key=f"local_camera_backend_{camera_name}",
                throttle_sec=10,
            )
            return cap

        cap.release()

    return None


def _run_camera_pipeline(camera: Camera, target_fps: int = 10, emit_jpeg=None, should_stop=None):
    cv2 = _safe_import_cv2()
    np = _safe_import_numpy()

    if cv2 is None or np is None:
        _log_line("âŒ Falta cv2 o numpy", key="deps_missing", throttle_sec=5)
        return

    preload_camera_models(async_load=True)
    risk_yolo_detector = None
    vision_event_detector = None
    ppe_model = None
    face_rec = None
    models_attached = False

    target_fps = _bounded_camera_fps(target_fps)
    camera_source = camera.get_video_source()
    camera_name = camera.nombre
    is_local_camera = isinstance(camera_source, int)
    analysis_cap = None
    analysis_reader = None

    if is_local_camera:
        # Camara local tipo webcam. En Windows algunos backends abren el
        # dispositivo pero devuelven frames corruptos, por eso se prueban varios.
        cap = _open_local_camera(cv2, camera_source, camera_name, target_fps)
    else:
        # CÃ¡mara IP / RTSP
        camera_source = str(camera_source).strip()

        # Opciones para reducir retraso en RTSP con OpenCV + FFMPEG
        if camera_source.lower().startswith("rtsp://"):
            rtsp_transport = str(getattr(settings, "RTSP_TRANSPORT", "udp") or "udp").lower()
            if rtsp_transport not in {"udp", "tcp"}:
                rtsp_transport = "udp"
            os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
                f"rtsp_transport;{rtsp_transport}|max_delay;100000|fflags;nobuffer|flags;low_delay"
            )

        preview_source, analysis_source = _rtsp_stream_sources(camera_source)
        cap = _open_rtsp_camera(cv2, preview_source)

        if analysis_source:
            analysis_cap = _open_rtsp_camera(cv2, analysis_source)
            if analysis_cap.isOpened():
                analysis_reader = LatestFrameReader(
                    analysis_cap,
                    f"{camera_name}-alta-resolucion",
                )
                analysis_reader.start()
                _log_line(
                    f"Doble flujo RTSP activo: {camera_name}",
                    key=f"dual_stream_{camera.id}",
                    throttle_sec=10,
                )
            else:
                analysis_cap.release()
                analysis_cap = None
                _log_line(
                    f"Flujo principal no disponible; usando flujo normal: {camera_name}",
                    key=f"dual_stream_fallback_{camera.id}",
                    throttle_sec=10,
                )

    if cap is None or not cap.isOpened():
        if analysis_reader is not None:
            analysis_reader.stop()
        if analysis_cap is not None:
            analysis_cap.release()
        _log_line(f"âŒ No se pudo abrir la cÃ¡mara: {camera_name}", key=f"cam_fail_{camera.id}", throttle_sec=10)
        return

    latest_reader = LatestFrameReader(cap, camera_name)
    latest_reader.start()

    face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")

    # Face recognition setup
    last_face_db_sync = 0.0
    known_face_encodings = []
    known_face_metadata = []
    current_faces = []
    last_detected_faces = []
    unauthorized_face_memory = {}
    ppe_violation_memory = {}
    recent_area_context = {}

    frame_counter = 0
    last_ppe_event_frame = -999

    risk_yolo_frame_interval = max(1, int(getattr(settings, "RISK_YOLO_FRAME_INTERVAL", 3)))
    face_recognition_frame_interval = max(1, int(getattr(settings, "FACE_RECOGNITION_FRAME_INTERVAL", 12)))
    face_detection_frame_interval = max(1, int(getattr(settings, "FACE_DETECTION_FRAME_INTERVAL", 8)))
    ppe_frame_interval = max(1, int(getattr(settings, "PPE_FRAME_INTERVAL", 30)))
    frame_interval = 1.0 / float(target_fps)
    next_frame_at = time.monotonic()
    first_no_frame_at = None
    source_resolution_logged = False

    _log_line(
        f"ðŸŸ¢ Streaming iniciado: {camera_name} (fps={target_fps})",
        key=f"stream_start_{camera.id}",
        throttle_sec=2
    )

    try:
        while True:
            if should_stop is not None and should_stop():
                break

            first_no_frame_at = None

            now = time.monotonic()

            if not models_attached and _MODEL_PRELOAD_DONE.is_set():
                risk_yolo_detector, ppe_model, face_rec = _attach_preloaded_models()
                vision_event_detector = _load_vision_event_detector(key=f"camera:{camera.id}")
                models_attached = True

                if (
                    risk_yolo_detector is not None
                    or vision_event_detector is not None
                    or ppe_model is not None
                    or face_rec is not None
                ):
                    _log_line(
                        (
                            f"Modelos activos para {camera_name} | "
                            f"YOLOv8s riesgo: {'si' if risk_yolo_detector is not None else 'no'} "
                            f"| vision general: {'si' if vision_event_detector is not None else 'no'} "
                            f"(conf={getattr(settings, 'RISK_YOLO_CONF', 0.35)}, "
                            f"cada {risk_yolo_frame_interval} frames)"
                        ),
                        key=f"models_ready_{camera.id}",
                        throttle_sec=10,
                    )

            if now < next_frame_at:
                time.sleep(next_frame_at - now)

            next_frame_at = max(next_frame_at + frame_interval, time.monotonic() + 0.001)

            # Para cÃ¡maras RTSP, intentamos descartar frames viejos acumulados
            ok, frame = latest_reader.get_latest(
                max_age_seconds=getattr(settings, "RTSP_STALE_FRAME_SECONDS", 8)
            )

            if not ok:
                if first_no_frame_at is None:
                    first_no_frame_at = time.monotonic()

                _log_line(
                    f"âŒ No se pudo leer frame de {camera_name}",
                    key=f"frame_fail_{camera.id}",
                    throttle_sec=5
                )
                elapsed_without_frame = time.monotonic() - first_no_frame_at
                if elapsed_without_frame >= getattr(settings, "RTSP_INITIAL_FRAME_TIMEOUT_SECONDS", 15):
                    _log_line(
                        f"Streaming detenido por falta de frames: {camera_name}",
                        key=f"frame_timeout_{camera.id}",
                        throttle_sec=5,
                    )
                    break

                time.sleep(0.1)
                continue

            first_no_frame_at = None

            high_resolution_frame = frame
            if analysis_reader is not None:
                high_res_ok, high_res_candidate = analysis_reader.get_latest(
                    max_age_seconds=getattr(
                        settings,
                        "RTSP_HIGH_RES_STALE_FRAME_SECONDS",
                        3.0,
                    )
                )
                if high_res_ok:
                    high_resolution_frame = high_res_candidate

            # La fuente puede capturarse a 1080p/4MP, pero la vista y los
            # detectores generales conservan un cuadro ligero.
            frame = _resize_frame_to_width(
                cv2,
                frame,
                getattr(settings, "LIVE_VIDEO_FRAME_WIDTH", 640),
            )

            evidence_frame = high_resolution_frame
            if not source_resolution_logged:
                source_height, source_width = high_resolution_frame.shape[:2]
                _log_line(
                    f"Resolucion de analisis [{camera_name}]: "
                    f"{source_width}x{source_height} | vista en vivo: "
                    f"{frame.shape[1]}x{frame.shape[0]}",
                    key=f"camera_resolution_{camera.id}",
                )
                source_resolution_logged = True
            frame_counter += 1

            frame_height, frame_width = frame.shape[:2]
            detail_height, detail_width = high_resolution_frame.shape[:2]
            face_analysis_width = min(
                detail_width,
                max(320, int(getattr(settings, "FACE_ANALYSIS_WIDTH", 480))),
            )
            face_analysis_height = max(
                1,
                int(detail_height * (face_analysis_width / detail_width)),
            )
            small_frame = cv2.resize(
                high_resolution_frame,
                (face_analysis_width, face_analysis_height),
            )
            face_scale_x = frame_width / float(face_analysis_width)
            face_scale_y = frame_height / float(face_analysis_height)

            # Face detection and recognition
            if face_rec is not None:
                if frame_counter % face_recognition_frame_interval == 0:
                    # Sync authorized faces from database periodically (every 10 seconds)
                    if now - last_face_db_sync > 10.0:
                        known_face_encodings = []
                        known_face_metadata = []
                        try:
                            close_old_connections()
                            for person in AuthorizedPerson.objects.filter(is_active=True):
                                try:
                                    enc = json.loads(person.face_encoding)
                                    known_face_encodings.append(np.array(enc))
                                    known_face_metadata.append({
                                        "name": person.get_full_name(),
                                        "person": person
                                    })
                                except Exception as e:
                                    print(f"Error parsing encoding for {person}: {e}")
                        except Exception as e:
                            print(f"Error loading authorized persons: {e}")
                        last_face_db_sync = now

                    # Run recognition
                    rgb_small = cv2.cvtColor(small_frame, cv2.COLOR_BGR2RGB)
                    with _FACE_RECOGNITION_INFERENCE_LOCK:
                        face_locations = face_rec.face_locations(rgb_small, model="hog")
                        face_encodings = face_rec.face_encodings(
                            rgb_small,
                            face_locations,
                            num_jitters=1,
                        )

                    new_detected_faces = []
                    current_faces = []

                    for face_loc, face_encoding in zip(face_locations, face_encodings):
                        top, right, bottom, left = face_loc

                        x1 = int(left * face_scale_x)
                        y1 = int(top * face_scale_y)
                        x2 = int(right * face_scale_x)
                        y2 = int(bottom * face_scale_y)
                        face_width = x2 - x1
                        face_height = y2 - y1

                        if (
                            face_width < frame_width * getattr(settings, "FACE_MIN_BOX_WIDTH_RATIO", 0.035)
                            or face_height < frame_height * getattr(settings, "FACE_MIN_BOX_HEIGHT_RATIO", 0.055)
                        ):
                            continue

                        cx = (x1 + x2) // 2
                        cy = (y1 + y2) // 2

                        # Try to match with a previously tracked face.
                        tracked_face = None
                        best_dist = 999.0
                        for f in last_detected_faces:
                            dist = np.sqrt((cx - f["center"][0])**2 + (cy - f["center"][1])**2)
                            if dist < 80.0 and dist < best_dist:
                                best_dist = dist
                                tracked_face = f

                        # Perform database recognition
                        name = "Desconocido"
                        is_authorized = False
                        person_obj = None

                        if known_face_encodings:
                            tolerance = getattr(settings, "FACE_RECOGNITION_TOLERANCE", 0.65)
                            matches = face_rec.compare_faces(
                                known_face_encodings,
                                face_encoding,
                                tolerance=tolerance,
                            )
                            if True in matches:
                                face_distances = face_rec.face_distance(known_face_encodings, face_encoding)
                                best_match_idx = np.argmin(face_distances)
                                if matches[best_match_idx]:
                                    is_authorized = True
                                    person_obj = known_face_metadata[best_match_idx]["person"]
                                    name = known_face_metadata[best_match_idx]["name"]

                        # Temporal smoothing / Hysteresis:
                        # If current frame says unauthorized, but we recognized them as authorized recently (last 4 seconds),
                        # preserve their authorized status to avoid flashing.
                        if not is_authorized and tracked_face is not None:
                            auth_memory_seconds = getattr(settings, "FACE_AUTH_MEMORY_SECONDS", 10.0)
                            if tracked_face["is_authorized"] and (now - tracked_face["last_authorized_ts"] < auth_memory_seconds):
                                is_authorized = True
                                person_obj = tracked_face["person_obj"]
                                name = tracked_face["name"]

                        # Determine last authorized timestamp
                        last_auth_ts = tracked_face["last_authorized_ts"] if (tracked_face and tracked_face["is_authorized"]) else 0.0
                        if is_authorized:
                            last_auth_ts = now

                        new_detected_faces.append({
                            "center": (cx, cy),
                            "name": name,
                            "is_authorized": is_authorized,
                            "person_obj": person_obj,
                            "last_authorized_ts": last_auth_ts,
                            "coords": (x1, y1, x2, y2),
                            "last_seen": now
                        })

                        if is_authorized:
                            unauthorized_face_memory.clear()
                            current_faces.append((x1, y1, x2, y2, f"Autorizado: {name}", (0, 255, 0)))

                            # El reconocimiento autorizado se conserva solo en el log y
                            # en memoria para asociar correctamente los eventos de EPP.
                            _log_line(
                                f"FACE [{camera_name}]: âœ… Autorizado: {name}",
                                key=f"face_rec_log_{camera.id}_{person_obj.id}",
                                throttle_sec=15.0
                            )
                        else:
                            face_key = f"{camera.id}:{cx // 40}:{cy // 40}"
                            memory_entry = unauthorized_face_memory.get(
                                face_key,
                                {"count": 0, "last_seen": now},
                            )
                            memory_entry["count"] += 1
                            memory_entry["last_seen"] = now
                            unauthorized_face_memory[face_key] = memory_entry
                            is_confirmed_unauthorized = (
                                memory_entry["count"]
                                >= getattr(settings, "FACE_UNAUTHORIZED_CONFIRMATION_FRAMES", 3)
                            )
                            should_display_verifying = (
                                memory_entry["count"]
                                >= getattr(settings, "FACE_UNAUTHORIZED_DISPLAY_FRAMES", 2)
                            )

                            if not is_confirmed_unauthorized:
                                if should_display_verifying:
                                    current_faces.append((x1, y1, x2, y2, "Verificando rostro...", (0, 165, 255)))
                                continue

                            current_faces.append((x1, y1, x2, y2, "NO AUTORIZADO", (0, 0, 255)))

                            # Log and alert
                            _log_line(
                                f"FACE [{camera_name}]: âŒ Persona no autorizada detectada",
                                key=f"face_unauth_log_{camera.id}",
                                throttle_sec=15.0
                            )
                            event_key = f"unauthorized_face_event_{camera.id}"
                            if can_save_event(event_key, seconds=30):
                                try:
                                    create_security_event(
                                        event_type="intrusion",
                                        details="Persona no autorizada detectada en el Ã¡rea monitoreada",
                                        frame=evidence_frame.copy(),
                                        camera=camera,
                                        epp_correcto=False,
                                        severity="ALTO",
                                    )
                                except Exception as e:
                                    print(f"Error saving unauthorized event: {e}")

                    for key in list(unauthorized_face_memory.keys()):
                        if now - unauthorized_face_memory[key].get("last_seen", 0.0) > 8.0:
                            del unauthorized_face_memory[key]

                    # Mantener rostros detectados recientemente para que PPE pueda asociarlos
                    # aunque face_recognition no los vea en este frame exacto.
                    last_detected_faces = _merge_recent_faces(
                        old_faces=last_detected_faces,
                        new_faces=new_detected_faces,
                        now=now,
                    )
            else:
                # Fallback to Haar Cascade
                if frame_counter % face_detection_frame_interval == 0:
                    gray = cv2.cvtColor(small_frame, cv2.COLOR_BGR2GRAY)
                    faces = face_cascade.detectMultiScale(gray, 1.1, 4)

                    if len(faces) > 0:
                        _log_line(
                            f"FACE [{camera_name}]: {len(faces)} rostro(s)",
                            key=f"face_count_{camera.id}",
                            throttle_sec=0.8
                        )

                    current_faces = []
                    for (x, y, w, h) in faces:
                        x1 = int(x * face_scale_x)
                        y1 = int(y * face_scale_y)
                        x2 = int((x + w) * face_scale_x)
                        y2 = int((y + h) * face_scale_y)
                        current_faces.append((x1, y1, x2, y2, "Rostro", (255, 0, 0)))

            # Draw current faces bounding boxes and labels
            for x1, y1, x2, y2, label, color in current_faces:
                cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
                cv2.putText(
                    frame,
                    label,
                    (x1, max(y1 - 10, 20)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.6,
                    color,
                    2,
                )

            # YOLOv8s preentrenado para objetos de riesgo.
            if frame_counter % risk_yolo_frame_interval == 0 and risk_yolo_detector is not None:
                try:
                    risk_detections = risk_yolo_detector.detect(frame)
                    frame = risk_yolo_detector.draw_detections(frame, risk_detections)

                    for detection in risk_detections:
                        if detection.internal_label in {"cat", "dog", "bird"}:
                            risk_log_message = (
                                f"ALERTA [{camera_name}]: {detection.message} "
                                f"({detection.confidence:.2f})"
                            )
                            recent_area_context["animal"] = {
                                "message": detection.message,
                                "last_seen": now,
                                "confidence": detection.confidence,
                            }
                        else:
                            risk_log_message = (
                                f"OBJETO [{camera_name}]: {detection.message} "
                                f"({detection.confidence:.2f}) | Categoria: "
                                f"{detection.category} | Nivel: {detection.severity}"
                            )

                        _log_line(
                            risk_log_message,
                            key=f"risk_obj_{camera.id}_{detection.internal_label}",
                            throttle_sec=0.25,
                        )

                        if not detection.should_alert:
                            continue

                        event_key = (
                            f"{detection.event_type}_camera_{camera.id}_"
                            f"{detection.internal_label}"
                        )

                        if can_save_event(event_key, seconds=20):
                            details = (
                                f"{detection.message} | Categoria: {detection.category} "
                                f"| Nivel: {detection.severity} "
                                f"| Confianza: {detection.confidence:.2f}"
                            )

                            create_security_event(
                                event_type=detection.event_type,
                                details=details,
                                frame=evidence_frame.copy(),
                                user=None,
                                camera=camera,
                                epp_correcto=False,
                                severity=detection.severity,
                            )

                            _log_line(
                                f"Evidencia guardada [{camera_name}]: "
                                f"{detection.internal_label} ({detection.severity})",
                                key=(
                                    f"risk_evidence_{camera.id}_{detection.event_type}_"
                                    f"{detection.internal_label}"
                                ),
                                throttle_sec=2,
                            )

                except Exception as e:
                    _log_line(
                        f"Error YOLOv8s objetos [{camera_name}]: {e}",
                        key=f"risk_yolo_detect_err_{camera.id}",
                        throttle_sec=5,
                    )

            if vision_event_detector is not None:
                try:
                    vision_event_detector.submit(
                        frame,
                        identities=last_detected_faces,
                    )
                    vision_events, alert_events = vision_event_detector.get_latest()

                    for event in vision_events:
                        if event.event_type != "phone_usage":
                            continue

                        person_text = f" | {event.person_name}" if event.person_name else ""
                        confidence_text = (
                            f" ({event.confidence:.2f})"
                            if event.confidence is not None
                            else ""
                        )
                        duration_text = (
                            f" | tiempo={event.duration_seconds:.1f}s"
                            if event.duration_seconds is not None
                            else ""
                        )

                        _log_line(
                            f"PHONE [{camera_name}]: celular detectado{confidence_text}{person_text}{duration_text}",
                            key=f"phone_live_{camera.id}_{event.person_name or 'unknown'}",
                            throttle_sec=3.0,
                        )

                    for event in alert_events:
                        if event.confidence is not None:
                            confidence_text = f" ({event.confidence:.2f})"
                        else:
                            confidence_text = ""

                        person_text = f" | {event.person_name}" if event.person_name else ""
                        if event.event_type == "dangerous_object":
                            log_message = (
                                f"ALERTA [{camera_name}]: objeto peligroso "
                                f"{event.object_label or ''}{confidence_text} | posibles cortes"
                            )
                        elif event.event_type == "phone_usage":
                            log_message = (
                                f"ALERTA [{camera_name}]: distraccion por uso de celular"
                                f"{confidence_text}{person_text}"
                            )
                        elif event.event_type == "fall_detected":
                            log_message = (
                                f"ALERTA [{camera_name}]: movimiento detectado{person_text}"
                            )
                        else:
                            log_message = (
                                f"VISION [{camera_name}]: {event.event_type}{confidence_text} "
                                f"| Nivel {event.severity}{person_text}"
                            )

                        _log_line(
                            log_message,
                            key=f"vision_{camera.id}_{event.event_type}_{event.object_label or 'event'}",
                            throttle_sec=0.5,
                        )

                        if event.event_type == "dangerous_object" and event.object_label:
                            event_key = (
                                f"{event.event_type}_camera_{camera.id}_"
                                f"{event.object_label}"
                            )
                        elif event.object_label:
                            event_key = (
                                f"vision_{event.event_type}_camera_{camera.id}_"
                                f"{event.object_label}"
                            )
                        else:
                            event_key = f"vision_{event.event_type}_camera_{camera.id}"

                        if can_save_event(event_key, seconds=getattr(settings, "EVENT_COOLDOWN_SECONDS", 10)):
                            create_security_event(
                                event_type=event.event_type,
                                details=event.details,
                                frame=evidence_frame.copy(),
                                user=None,
                                camera=camera,
                                authorized_person=event.authorized_person,
                                epp_correcto=False,
                                severity=event.severity,
                            )

                            _log_line(
                                f"Evidencia guardada [{camera_name}]: "
                                f"{event.event_type} ({event.severity})",
                                key=f"vision_evidence_{camera.id}_{event.event_type}",
                                throttle_sec=2,
                            )

                    frame = vision_event_detector.draw(frame, vision_events)

                except Exception as e:
                    _log_line(
                        f"Error detector visual [{camera_name}]: {e}",
                        key=f"vision_event_detect_err_{camera.id}",
                        throttle_sec=5,
                    )

            # PPE
            if ppe_model is not None and frame_counter % ppe_frame_interval == 0:
                try:
                    ppe_source_frame = high_resolution_frame
                    with _PPE_INFERENCE_LOCK:
                        res = ppe_model(
                            ppe_source_frame,
                            verbose=False,
                            imgsz=PPE_INFERENCE_IMGSZ,
                            conf=PPE_MODEL_CONFIDENCE,
                        )[0]
                    boxes = res.boxes
                    names = res.names
                    supported_required_ppe_items = _get_supported_required_ppe_items(names)

                    persons = []
                    items = []
                    observed_ppe_violation_keys = set()

                    for b in boxes:
                        cls_id = int(b.cls[0])
                        label = _normalize_ppe_label(names.get(cls_id, cls_id))
                        conf = float(b.conf[0])
                        source_box = tuple(map(int, b.xyxy[0].tolist()))
                        x1, y1, x2, y2 = _scale_box_between_frames(
                            source_box,
                            ppe_source_frame.shape,
                            frame.shape,
                        )

                        if not _passes_ppe_confidence(label, conf):
                            continue

                        if label == "person":
                            if _is_valid_ppe_person(
                                frame.shape,
                                (x1, y1, x2, y2),
                                conf,
                            ):
                                persons.append((x1, y1, x2, y2, conf))
                        else:
                            items.append((label, conf, x1, y1, x2, y2))

                    for (px1, py1, px2, py2, _person_conf) in persons:
                        if not _is_ppe_person_corroborated(
                            (px1, py1, px2, py2),
                            vision_event_detector,
                            last_detected_faces,
                            now,
                        ):
                            continue

                        identity = _match_identity_to_person_box(
                            (px1, py1, px2, py2),
                            last_detected_faces
                        )

                        # Si no pudo asociar por la caja del rostro,
                        # pero solo hay una persona detectada por PPE,
                        # usa el Ãºltimo rostro reconocido recientemente.
                        if identity is None and len(persons) == 1:
                            identity = _get_recent_identity(last_detected_faces, now)

                        person_name, auth_status, authorized_person = _get_identity_text(identity)

                        present = set()
                        negatives = set()

                        for (label, conf, x1, y1, x2, y2) in items:
                            if _is_ppe_item_inside_person(
                                (px1, py1, px2, py2),
                                (x1, y1, x2, y2)
                            ):
                                present.add(label)

                                if label.startswith("no-"):
                                    negatives.add(label)

                        if negatives:
                            base_msg = "âš  Indumentaria incorrecta: " + ", ".join(sorted([x.upper() for x in negatives]))
                            msg = f"{base_msg} | Nivel: ALTO | Persona: {person_name} | Estado: {auth_status}"
                            animal_context = recent_area_context.get("animal")
                            if animal_context and now - animal_context.get("last_seen", 0.0) <= 10.0:
                                msg += (
                                    " | Evento adicional: no autorizado identificado: "
                                    f"{animal_context['message']}"
                                )
                            violation_key = _ppe_violation_key(
                                camera.id,
                                (px1, py1, px2, py2),
                                "negative",
                                base_msg,
                            )
                            observed_ppe_violation_keys.add(violation_key)
                            is_confirmed, confirmation_count = _track_ppe_violation(
                                ppe_violation_memory,
                                violation_key,
                                now,
                            )

                            if not is_confirmed:
                                _log_line(
                                    f"PPE [{camera_name}]: verificando {base_msg} ({confirmation_count}/{PPE_CONFIRMATION_FRAMES})",
                                    key=f"ppe_pending_neg_{camera.id}_{_safe_event_key(base_msg)}",
                                    throttle_sec=1.2
                                )

                                cv2.rectangle(frame, (px1, py1), (px2, py2), (0, 165, 255), 2)

                                cv2.putText(
                                    frame,
                                    "Verificando EPP...",
                                    (px1, max(py1 - 10, 20)),
                                    cv2.FONT_HERSHEY_SIMPLEX,
                                    0.7,
                                    (0, 165, 255),
                                    2
                                )

                                continue

                            _log_line(
                                f"PPE [{camera_name}]: {msg}",
                                key=f"ppe_neg_{camera.id}",
                                throttle_sec=0.4
                            )

                            event_key = f"ppe_incorrect_{camera.id}_{_safe_event_key(person_name)}_{_safe_event_key(base_msg)}"

                            if can_save_event(event_key, seconds=25):
                                create_security_event(
                                    event_type="ppe_missing",
                                    details=msg,
                                    frame=evidence_frame.copy(),
                                    camera=camera,
                                    authorized_person=authorized_person,
                                    epp_correcto=False,
                                    severity="ALTO",
                                )

                            cv2.rectangle(frame, (px1, py1), (px2, py2), (0, 255, 255), 2)

                            cv2.putText(
                                frame,
                                msg,
                                (px1, max(py1 - 10, 20)),
                                cv2.FONT_HERSHEY_SIMPLEX,
                                0.7,
                                (0, 255, 255),
                                2
                            )

                            continue

                        missing = _get_missing_ppe_items(
                            present,
                            supported_required_ppe_items,
                        )

                        if missing:
                            missing_description = ", ".join(
                                PPE_ITEM_DISPLAY_NAMES.get(item, item)
                                for item in missing
                            )
                            base_msg = f"âš  Falta EPP: {missing_description}"
                            msg = f"{base_msg} | Nivel: ALTO | Persona: {person_name} | Estado: {auth_status}"
                            animal_context = recent_area_context.get("animal")
                            if animal_context and now - animal_context.get("last_seen", 0.0) <= 10.0:
                                msg += (
                                    " | Evento adicional: no autorizado identificado: "
                                    f"{animal_context['message']}"
                                )
                            violation_key = _ppe_violation_key(
                                camera.id,
                                (px1, py1, px2, py2),
                                "missing",
                                base_msg,
                            )
                            observed_ppe_violation_keys.add(violation_key)
                            is_confirmed, confirmation_count = _track_ppe_violation(
                                ppe_violation_memory,
                                violation_key,
                                now,
                            )

                            if not is_confirmed:
                                _log_line(
                                    f"PPE [{camera_name}]: verificando {base_msg} ({confirmation_count}/{PPE_CONFIRMATION_FRAMES})",
                                    key=f"ppe_pending_missing_{camera.id}_{_safe_event_key(base_msg)}",
                                    throttle_sec=1.2
                                )

                                cv2.rectangle(frame, (px1, py1), (px2, py2), (0, 165, 255), 2)

                                cv2.putText(
                                    frame,
                                    "Verificando EPP...",
                                    (px1, max(py1 - 10, 20)),
                                    cv2.FONT_HERSHEY_SIMPLEX,
                                    0.7,
                                    (0, 165, 255),
                                    2
                                )

                                continue

                            _log_line(
                                f"PPE [{camera_name}]: {msg}",
                                key=f"ppe_missing_{camera.id}",
                                throttle_sec=0.4
                            )

                            event_key = f"ppe_missing_{camera.id}_{_safe_event_key(person_name)}_{_safe_event_key(base_msg)}"

                            if can_save_event(event_key, seconds=25):
                                create_security_event(
                                    event_type="ppe_missing",
                                    details=msg,
                                    frame=evidence_frame.copy(),
                                    camera=camera,
                                    authorized_person=authorized_person,
                                    epp_correcto=False,
                                    severity="ALTO",
                                )

                            cv2.rectangle(frame, (px1, py1), (px2, py2), (0, 255, 255), 2)

                            cv2.putText(
                                frame,
                                msg,
                                (px1, max(py1 - 10, 20)),
                                cv2.FONT_HERSHEY_SIMPLEX,
                                0.7,
                                (0, 255, 255),
                                2
                            )

                        else:
                            _log_line(
                                f"PPE [{camera_name}]: âœ… EPP OK",
                                key=f"ppe_ok_{camera.id}",
                                throttle_sec=1.2
                            )

                            cv2.rectangle(frame, (px1, py1), (px2, py2), (0, 255, 0), 2)

                            cv2.putText(
                                frame,
                                "EPP OK",
                                (px1, max(py1 - 10, 20)),
                                cv2.FONT_HERSHEY_SIMPLEX,
                                0.7,
                                (0, 255, 0),
                                2
                            )

                    _prune_ppe_violations(
                        ppe_violation_memory,
                        observed_ppe_violation_keys,
                        now,
                    )

                except Exception as e:
                    _log_line(
                        f"âŒ Error PPE detect [{camera_name}]: {e}",
                        key=f"ppe_detect_err_{camera.id}",
                        throttle_sec=5
                    )

            ret, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 70])

            if not ret:
                continue

            if emit_jpeg is not None:
                emit_jpeg(buffer.tobytes())

    finally:
        if latest_reader is not None:
            latest_reader.stop()
        if analysis_reader is not None:
            analysis_reader.stop()

        cap.release()
        if analysis_cap is not None:
            analysis_cap.release()

        _log_line(
            f"ðŸŸ  Streaming detenido: {camera_name}",
            key=f"stream_stop_{camera.id}",
            throttle_sec=2
        )

CAMERA_WORKER_IDLE_SECONDS = 90.0
CAMERA_SIGNAL_TIMEOUT_SECONDS = 15.0

_CAMERA_WORKERS: dict[int, "CameraStreamWorker"] = {}
_CAMERA_WORKERS_LOCK = Lock()


class CameraStreamWorker:
    def __init__(self, camera: Camera, target_fps: int, keep_alive: bool = False):
        self.camera = camera
        self.camera_id = camera.id
        self.camera_source = camera.source
        self.camera_name = camera.nombre
        self.target_fps = _bounded_camera_fps(target_fps)
        self.keep_alive = keep_alive
        self._frame_lock = Lock()
        self._latest_jpeg = None
        self._latest_at = 0.0
        self._last_client_at = time.monotonic()
        self._stop_event = Event()
        self._finished = Event()
        self._error = None
        self._thread = Thread(
            target=self._run,
            name=f"camera-stream-{self.camera_id}",
            daemon=True,
        )

    def start(self):
        self._thread.start()

    def matches(self, camera: Camera) -> bool:
        return self.camera_source == camera.source and self.camera_name == camera.nombre

    def is_alive(self) -> bool:
        return self._thread.is_alive() and not self._finished.is_set()

    def touch(self):
        self._last_client_at = time.monotonic()

    def enable_keep_alive(self):
        self.keep_alive = True
        self.touch()

    def stop(self):
        self._stop_event.set()

    def should_stop(self) -> bool:
        if self._stop_event.is_set():
            return True

        if self.keep_alive:
            return False

        idle_for = time.monotonic() - self._last_client_at
        return idle_for > CAMERA_WORKER_IDLE_SECONDS

    def publish_frame(self, jpeg_bytes: bytes):
        with self._frame_lock:
            self._latest_jpeg = jpeg_bytes
            self._latest_at = time.monotonic()

    def get_frame(self):
        with self._frame_lock:
            return self._latest_jpeg

    def get_latest_at(self):
        with self._frame_lock:
            return self._latest_at

    def clear_frame(self):
        with self._frame_lock:
            self._latest_jpeg = None
            self._latest_at = 0.0

    def has_fresh_frame(self):
        with self._frame_lock:
            if self._latest_jpeg is None or not self._latest_at:
                return False

            return (time.monotonic() - self._latest_at) <= CAMERA_SIGNAL_TIMEOUT_SECONDS

    def has_stopped(self) -> bool:
        return self._finished.is_set()

    def get_error(self):
        return self._error

    def _run(self):
        try:
            _run_camera_pipeline(
                camera=self.camera,
                target_fps=self.target_fps,
                emit_jpeg=self.publish_frame,
                should_stop=self.should_stop,
            )
        except Exception as e:
            self._error = str(e)
            _log_line(
                f"Error en worker de camara {self.camera_name}: {e}",
                key=f"worker_error_{self.camera_id}",
                throttle_sec=5,
            )
        finally:
            self.clear_frame()
            close_old_connections()
            self._finished.set()


def _get_or_start_camera_worker(
    camera: Camera,
    target_fps: int,
    keep_alive: bool = False,
) -> CameraStreamWorker:
    with _CAMERA_WORKERS_LOCK:
        worker = _CAMERA_WORKERS.get(camera.id)

        if worker is None or not worker.is_alive() or not worker.matches(camera):
            if worker is not None:
                worker.stop()

            worker = CameraStreamWorker(
                camera=camera,
                target_fps=target_fps,
                keep_alive=keep_alive,
            )
            _CAMERA_WORKERS[camera.id] = worker
            worker.start()
        elif keep_alive:
            worker.enable_keep_alive()

        worker.touch()
        return worker


def autostart_active_camera_workers(target_fps: int = 8):
    started = 0
    skipped = 0

    try:
        close_old_connections()
        cameras = Camera.objects.filter(is_active=True).order_by("id")

        for camera in cameras:
            source = str(camera.source or "").strip()

            if source.startswith("push://"):
                skipped += 1
                _log_line(
                    f"Autoinicio omitido para camara no local: {camera.nombre}",
                    key=f"camera_autostart_skip_{camera.id}",
                    throttle_sec=10,
                )
                continue

            _get_or_start_camera_worker(
                camera=camera,
                target_fps=target_fps,
                keep_alive=True,
            )
            started += 1

        _log_line(
            f"Autoinicio de camaras activo: {started} camara(s), {skipped} omitida(s)",
            key="camera_autostart",
            throttle_sec=10,
        )
    except Exception as e:
        _log_line(
            f"Error autoiniciando camaras: {e}",
            key="camera_autostart_error",
            throttle_sec=10,
        )

    return started


def gen_frames(camera: Camera, target_fps: int = None):
    target_fps = _bounded_camera_fps(target_fps)

    worker = _get_or_start_camera_worker(camera, target_fps)
    frame_interval = 1.0 / float(target_fps)

    while True:
        worker.touch()

        if worker.has_stopped():
            error = worker.get_error()

            if error:
                _log_line(
                    f"Stream finalizado por error [{camera.nombre}]: {error}",
                    key=f"stream_error_{camera.id}",
                    throttle_sec=5,
                )

            break

        jpeg = worker.get_frame()

        if jpeg is None:
            time.sleep(0.05)
            continue

        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n"
        )

        time.sleep(frame_interval)


def _get_request_fps(request):
    return _bounded_camera_fps(request.GET.get("fps"))
    
def _camera_stream_response(camera: Camera, fps: int):
    return StreamingHttpResponse(
        gen_frames(camera=camera, target_fps=fps),
        content_type="multipart/x-mixed-replace;boundary=frame",
    )

@login_required(login_url="/login/")
def video_feed(request, camera_id):
    if not is_admin_user(request.user):
        return _json_forbidden("Solo un administrador puede visualizar el stream de camara.")

    camera = get_object_or_404(Camera, id=camera_id, is_active=True)
    fps = _get_request_fps(request)
    cv2 = _safe_import_cv2()

    if cv2 is None:
        return JsonResponse(
            {"success": False, "message": "OpenCV no estÃ¡ instalado."},
            status=400
        )

    return _camera_stream_response(camera, fps)

@login_required(login_url="/login/")
def video_feed_default(request):
    if not is_admin_user(request.user):
        return _json_forbidden("Solo un administrador puede visualizar el stream de camara.")

    camera = Camera.objects.filter(is_active=True).order_by("id").first()

    if camera is None:
        return JsonResponse(
            {"success": False, "message": "No hay cÃ¡maras activas configuradas."},
            status=404
        )

    fps = _get_request_fps(request)
    cv2 = _safe_import_cv2()

    if cv2 is None:
        return JsonResponse(
            {"success": False, "message": "OpenCV no estÃ¡ instalado."},
            status=400
        )

    return _camera_stream_response(camera, fps)

@login_required(login_url="/login/")
def camera_status(request, camera_id):
    if not is_admin_user(request.user):
        return _json_forbidden("Solo un administrador puede consultar el estado de camara.")

    """
    Estado real de la cÃ¡mara:
    - active: habilitada y con frames recientes
    - inactive: desactivada en base de datos
    - no_signal: habilitada, pero sin seÃ±al real
    """
    camera = get_object_or_404(Camera, id=camera_id)

    if not camera.is_active:
        return JsonResponse({
            "success": True,
            "camera_id": camera.id,
            "name": camera.nombre,
            "status": "inactive",
            "label": "Inactiva",
            "message": "La cÃ¡mara estÃ¡ desactivada en el sistema.",
            "tone": "secondary",
        })

    with _CAMERA_WORKERS_LOCK:
        worker = _CAMERA_WORKERS.get(camera.id)

    if worker is None:
        return JsonResponse({
            "success": True,
            "camera_id": camera.id,
            "name": camera.nombre,
            "status": "no_signal",
            "label": "Sin seÃ±al",
            "message": "La cÃ¡mara estÃ¡ habilitada, pero todavÃ­a no se ha iniciado el flujo.",
            "tone": "warning",
        })

    if worker.has_stopped():
        return JsonResponse({
            "success": True,
            "camera_id": camera.id,
            "name": camera.nombre,
            "status": "no_signal",
            "label": "Sin seÃ±al",
            "message": "El flujo de la cÃ¡mara estÃ¡ detenido o no pudo iniciarse.",
            "tone": "danger",
        })

    latest_at = worker.get_latest_at()

    if not latest_at:
        return JsonResponse({
            "success": True,
            "camera_id": camera.id,
            "name": camera.nombre,
            "status": "no_signal",
            "label": "Sin seÃ±al",
            "message": "La cÃ¡mara estÃ¡ habilitada, pero no estÃ¡ entregando imagen.",
            "tone": "warning",
        })

    seconds_without_signal = time.monotonic() - latest_at

    if seconds_without_signal > CAMERA_SIGNAL_TIMEOUT_SECONDS:
        return JsonResponse({
            "success": True,
            "camera_id": camera.id,
            "name": camera.nombre,
            "status": "no_signal",
            "label": "Sin seÃ±al",
            "message": f"No se reciben frames desde hace {seconds_without_signal:.1f} segundos.",
            "tone": "danger",
            "seconds_without_signal": round(seconds_without_signal, 1),
        })

    if not worker.has_fresh_frame():
        return JsonResponse({
            "success": True,
            "camera_id": camera.id,
            "name": camera.nombre,
            "status": "no_signal",
            "label": "Sin seÃ±al",
            "message": "No hay un frame reciente disponible.",
            "tone": "danger",
        })

    return JsonResponse({
        "success": True,
        "camera_id": camera.id,
        "name": camera.nombre,
        "status": "active",
        "label": "Activa",
        "message": "La cÃ¡mara estÃ¡ activa y transmitiendo seÃ±al.",
        "tone": "success",
        "seconds_without_signal": round(seconds_without_signal, 1),
    })

@csrf_exempt
@login_required(login_url="/login/")
def register_face(request):
    if not is_admin_user(request.user):
        return _json_forbidden("Solo un administrador puede registrar rostros autorizados.")

    if request.method != "POST":
        return JsonResponse(
            {"success": False, "message": "MÃ©todo no permitido."},
            status=405
        )

    try:
        face_recognition = _safe_import_face_recognition()

        if face_recognition is None:
            return JsonResponse(
                {
                    "success": False,
                    "message": "La librerÃ­a face_recognition no estÃ¡ instalada."
                },
                status=400
            )

        if not request.user.is_authenticated:
            return JsonResponse(
                {
                    "success": False,
                    "message": "Debes iniciar sesiÃ³n para registrar un rostro."
                },
                status=401
            )

        nombres = request.POST.get("nombres", "").strip()
        apellidos = request.POST.get("apellidos", "").strip()
        celular = request.POST.get("celular", "").strip()
        correo = request.POST.get("correo", "").strip().lower()
        cargo = request.POST.get("cargo", "").strip()

        image_file = request.FILES.get("image")

        if not nombres:
            return JsonResponse(
                {"success": False, "message": "Ingresa los nombres de la persona."},
                status=400
            )

        if not apellidos:
            return JsonResponse(
                {"success": False, "message": "Ingresa los apellidos de la persona."},
                status=400
            )

        if not correo:
            return JsonResponse(
                {"success": False, "message": "Ingresa el correo de la persona."},
                status=400
            )

        try:
            validate_email(correo)
        except ValidationError:
            return JsonResponse(
                {"success": False, "message": "Ingresa un correo electrÃ³nico vÃ¡lido."},
                status=400
            )

        if not cargo:
            return JsonResponse(
                {"success": False, "message": "Ingresa el cargo de la persona."},
                status=400
            )

        if not image_file:
            return JsonResponse(
                {"success": False, "message": "Selecciona una imagen del rostro."},
                status=400
            )

        max_upload_size = 10 * 1024 * 1024

        if image_file.size > max_upload_size:
            return JsonResponse(
                {
                    "success": False,
                    "message": "La fotografÃ­a supera el lÃ­mite de 10 MB. Selecciona una imagen mÃ¡s liviana."
                },
                status=400
            )

        try:
            image_file.seek(0)

            with Image.open(image_file) as source_image:
                normalized_image = ImageOps.exif_transpose(source_image).convert("RGB")
                normalized_image.thumbnail((800, 800), Image.Resampling.LANCZOS)

                normalized_buffer = BytesIO()
                normalized_image.save(normalized_buffer, format="JPEG", quality=90, optimize=True)
                normalized_bytes = normalized_buffer.getvalue()
        except (UnidentifiedImageError, OSError, ValueError):
            return JsonResponse(
                {
                    "success": False,
                    "message": "El archivo seleccionado no es una imagen vÃ¡lida. Usa JPG, PNG o WEBP."
                },
                status=400
            )

        image_data = face_recognition.load_image_file(BytesIO(normalized_bytes))

        with _FACE_RECOGNITION_INFERENCE_LOCK:
            face_locations = face_recognition.face_locations(image_data, model="hog")

            if len(face_locations) == 1:
                encodings = face_recognition.face_encodings(
                    image_data,
                    face_locations,
                    num_jitters=1,
                )
            else:
                encodings = []

        if not face_locations:
            return JsonResponse(
                {
                    "success": False,
                    "message": "No se detectÃ³ ningÃºn rostro en la imagen."
                },
                status=400
            )

        if len(face_locations) > 1:
            return JsonResponse(
                {
                    "success": False,
                    "message": "La imagen debe contener solo un rostro."
                },
                status=400
            )

        if not encodings:
            return JsonResponse(
                {
                    "success": False,
                    "message": "No se pudo generar la codificaciÃ³n facial."
                },
                status=400
            )

        encoding_json = json.dumps(encodings[0].tolist())

        normalized_file = ContentFile(normalized_bytes, name="authorized_face.jpg")
        face_image_path = save_authorized_face_image(normalized_file, correo)

        if not face_image_path:
            return JsonResponse(
                {
                    "success": False,
                    "message": "No se pudo guardar la fotografÃ­a. Revisa el almacenamiento del servidor."
                },
                status=500
            )

        try:
            with transaction.atomic():
                person, created = AuthorizedPerson.objects.update_or_create(
                    correo=correo,
                    defaults={
                        "nombres": nombres,
                        "apellidos": apellidos,
                        "celular": celular,
                        "cargo": cargo,
                        "face_encoding": encoding_json,
                        "face_image_path": face_image_path,
                        "registered_by": request.user,
                        "is_active": True,
                    }
                )
        except IntegrityError:
            return JsonResponse(
                {
                    "success": False,
                    "message": "No se pudo registrar la persona porque el correo ya estÃ¡ asociado a otro registro."
                },
                status=409
            )

        action = "registrado" if created else "actualizado"

        _log_line(
            f"âœ… Rostro autorizado {action}: {person.get_full_name()}",
            key=f"face_registered_{person.id}",
            throttle_sec=1.5
        )

        return JsonResponse(
            {
                "success": True,
                "message": f"Rostro {action} correctamente para {person.get_full_name()}."
            }
        )

    except Exception as e:
        print("[ERROR] register_face:")
        print(traceback.format_exc())

        return JsonResponse(
            {
                "success": False,
                "message": f"Error interno al registrar el rostro: {str(e)}"
            },
            status=500
        )

@login_required(login_url="/login/")
def get_events(request):
    if not is_admin_user(request.user):
        return _json_forbidden("Solo un administrador puede consultar este listado.")

    events = list(
        SecurityEvent.objects.select_related(
            "authorized_person",
            "reviewed_by",
            "managed_by",
        ).order_by("-timestamp")[:50]
    )
    detection_counts = _detection_counts_for_people(
        [event.authorized_person_id for event in events]
    )
    data = [
        _event_payload(
            event,
            include_image_path=True,
            request_user=request.user,
            detection_counts=detection_counts,
        )
        for event in events
    ]
    return JsonResponse({"events": data})


@csrf_exempt
@login_required(login_url="/login/")
def mark_event_resolved(request, event_id):
    if not is_admin_user(request.user):
        return _json_forbidden("Solo un administrador puede gestionar eventos.")

    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "MÃ©todo no permitido"}, status=405)

    event = get_object_or_404(SecurityEvent, id=event_id)
    event.resolved = True
    event.managed_by = request.user
    event.managed_at = timezone.now()
    event.save(update_fields=["resolved", "managed_by", "managed_at"])
    _log_line(f"âœ… Evento resuelto: {event_id}", key=f"ev_res_{event_id}", throttle_sec=0.5)
    return JsonResponse({"status": "success"})


@login_required(login_url="/login/")
def get_security_events(request):
    events = _filtered_security_events_queryset(request)
    severity = _requested_alert_level(request.GET.get("severity") or request.GET.get("priority"))
    max_events_to_scan = 1000 if severity else 50
    today_start, tomorrow_start = _local_day_bounds(timezone.localdate().isoformat())
    daily_events = _filtered_security_events_queryset(request).filter(
        timestamp__gte=today_start,
        timestamp__lt=tomorrow_start,
    )
    daily_summary = {
        "total_today": daily_events.count(),
        "high_priority_today": daily_events.filter(
            severity__in=("ALTO", "CRITICO")
        ).count(),
        "resolved_today": daily_events.filter(
            resolved=True,
        ).count(),
    }
    user_authorized_person = get_authorized_person_for_user(request.user)
    scanned_events = list(events[:max_events_to_scan])
    detection_counts = _detection_counts_for_people(
        [event.authorized_person_id for event in scanned_events]
    )
    events_data = []

    for event in scanned_events:
        payload = _event_payload(
            event,
            request_user=request.user,
            user_authorized_person=user_authorized_person,
            detection_counts=detection_counts,
        )

        if severity and payload["priority"] != severity:
            continue

        events_data.append(payload)

        if len(events_data) >= 50:
            break

    return JsonResponse({"events": events_data, "summary": daily_summary})


@login_required(login_url="/login/")
@require_POST
def review_security_event(request, event_id):
    event = get_object_or_404(
        SecurityEvent.objects.select_related(
            "authorized_person",
            "camera",
            "related_user",
            "reviewed_by",
            "managed_by",
        ),
        id=event_id,
    )
    user_authorized_person = get_authorized_person_for_user(request.user)

    if not _can_view_event_evidence(
        request.user,
        event,
        user_authorized_person=user_authorized_person,
    ):
        return _json_forbidden("Solo puedes revisar evidencias asociadas a tu persona autorizada.")

    event.reviewed_by = request.user
    event.reviewed_at = timezone.now()
    event.save(update_fields=["reviewed_by", "reviewed_at"])

    detection_counts = _detection_counts_for_people([event.authorized_person_id])

    return JsonResponse({
        "success": True,
        "event": _event_payload(
            event,
            request_user=request.user,
            user_authorized_person=user_authorized_person,
            detection_counts=detection_counts,
        )
    })


@login_required(login_url="/login/")
@require_POST
def retry_incident_email(request, event_id):
    if not is_admin_user(request.user):
        return _json_forbidden("Solo un administrador puede reenviar correos.")

    event = get_object_or_404(SecurityEvent, id=event_id)
    event.email_status = "PENDING"
    event.email_error = ""
    event.save(update_fields=["email_status", "email_error"])

    from core_apps.camera.services.incident_email import notify_incident_by_email

    sent = notify_incident_by_email(event.id)
    event.refresh_from_db()

    return JsonResponse({
        "success": sent,
        "message": (
            "Correo enviado correctamente."
            if sent
            else event.email_error or "No se pudo enviar el correo."
        ),
        "event": _event_payload(event, request_user=request.user),
    }, status=200 if sent else 400)


@csrf_exempt
def mark_event_as_resolved(request, event_id):
    return mark_event_resolved(request, event_id)


class CameraView(AdminRequiredMixin, TemplateView):
    template_name = "camera/camera.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        cameras = Camera.objects.all().order_by("id")
        selected_camera = cameras.filter(is_active=True).first() or cameras.first()

        context["segment"] = "camera"
        context["cameras"] = cameras
        context["selected_camera"] = selected_camera
        context["target_video_fps"] = getattr(settings, "SYSTEM_TARGET_VIDEO_FPS", 8)
        context["events_refresh_ms"] = getattr(settings, "SYSTEM_EVENTS_REFRESH_MS", 5000)
        context["live_log_refresh_ms"] = getattr(settings, "SYSTEM_LIVE_LOG_REFRESH_MS", 800)
        context["camera_status_refresh_ms"] = getattr(settings, "SYSTEM_CAMERA_STATUS_REFRESH_MS", 3000)

        return context


class AlertaView(LoginRequiredMixin, TemplateView):
    template_name = "alertas/alerta.html"
    login_url = "/login/"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["segment"] = "alerta"
        context["events_refresh_ms"] = getattr(settings, "SYSTEM_EVENTS_REFRESH_MS", 5000)
        return context

