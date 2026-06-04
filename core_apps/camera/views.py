from core_apps.camera.utils import cv2

import os
import json
import re
import time
import traceback
from collections import deque
from datetime import datetime, timedelta
from threading import Event, Lock, Thread

from django.conf import settings
from django.db import close_old_connections
from django.db.models import Count, Q
from django.http import JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView

from .models import AuthorizedPerson, SecurityEvent, Camera
from core_apps.camera.utils import (
    create_security_event,
    can_save_event,
    save_authorized_face_image,
)
from core_apps.camera.services.detection_rules import (
    build_detection_details,
    get_detection_rule,
    get_monitored_model_labels,
    get_rule_color,
    should_generate_detection_event,
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

    level = str(value).strip().upper().replace("Í", "I")

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


def _event_detection_count(event, detection_counts=None):
    if not event.authorized_person_id:
        return 0
    if detection_counts is not None:
        return detection_counts.get(event.authorized_person_id, 0)
    return SecurityEvent.objects.filter(authorized_person_id=event.authorized_person_id).count()


def _detection_counts_for_people(person_ids):
    person_ids = [person_id for person_id in person_ids if person_id]
    if not person_ids:
        return {}

    return {
        item["authorized_person_id"]: item["total"]
        for item in (
            SecurityEvent.objects.filter(authorized_person_id__in=person_ids)
            .values("authorized_person_id")
            .annotate(total=Count("id"))
        )
    }


def _event_payload(
    event,
    include_image_path=False,
    request_user=None,
    user_authorized_person=None,
    detection_counts=None,
):
    level = event.risk_level or _extract_alert_level(event.details, event.event_type)
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
        "details": event.details,
        "timestamp": _format_local_datetime(event.timestamp),
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
        "category": event.category,
        "object_label": event.object_label,
        "risk_level": event.risk_level,
        "confidence": event.confidence,
        "duration_seconds": event.duration_seconds,
        "should_alert": event.should_alert,
        **_alert_level_meta(level),
    }

    image_url = event.get_image_url() if can_view_evidence and hasattr(event, "get_image_url") else None

    if include_image_path:
        payload["image_path"] = image_url
    else:
        payload["image_url"] = image_url

    payload["camera"] = event.camera.nombre if event.camera else "Sin cámara"
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

    event_type = (request.GET.get("type") or request.GET.get("event_type") or "").strip()
    search = (request.GET.get("q") or "").strip()
    date_bounds = _local_day_bounds((request.GET.get("date") or "").strip())
    category = (request.GET.get("category") or "").strip()
    object_label = (request.GET.get("object_label") or "").strip()
    risk_level = _requested_alert_level(request.GET.get("severity") or request.GET.get("priority"))

    valid_event_types = {choice[0] for choice in SecurityEvent.EVENT_TYPES}
    if event_type in valid_event_types:
        queryset = queryset.filter(event_type=event_type)

    if category:
        queryset = queryset.filter(category=category)

    if object_label:
        queryset = queryset.filter(object_label__icontains=object_label)

    if risk_level:
        queryset = queryset.filter(risk_level=risk_level)

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
            | Q(category__icontains=search)
            | Q(object_label__icontains=search)
            | Q(risk_level__icontains=search)
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

        queryset = queryset.filter(search_filter)

    return queryset.order_by("-timestamp")


# =========================
# Helpers de identidad y asociación rostro/persona
# =========================
FACE_MEMORY_SECONDS = 6.0
FACE_MATCH_DISTANCE = 90.0
FACE_OVERLAP_THRESHOLD = 0.20

DEFAULT_OBJECT_CONFIDENCE = 0.20
PPE_INFERENCE_IMGSZ = 960
PPE_MODEL_CONFIDENCE = 0.25
PPE_CONFIRMATION_FRAMES = 2
PPE_ITEM_OVERLAP_THRESHOLD = 0.10
PPE_VIOLATION_TTL_SECONDS = 6.0
PPE_REQUIRED_ITEMS = ("mask", "gloves", "earmuffs")
PPE_CLASS_CONFIDENCE = {
    "person": 0.45,
    "hardhat": 0.45,
    "mask": 0.40,
    "gloves": 0.45,
    "earmuffs": 0.45,
    "safety vest": 0.45,
    "no-hardhat": 0.65,
    "no-mask": 0.65,
    "no-gloves": 0.65,
    "no-earmuffs": 0.65,
    "no-safety vest": 0.65,
    "safety cone": 0.50,
    "machinery": 0.50,
    "vehicle": 0.50,
}

ALERT_LEVELS = {
    "BAJO": {"className": "success", "icon": "fa-circle-check"},
    "MEDIO": {"className": "warning", "icon": "fa-circle-exclamation"},
    "ALTO": {"className": "danger", "icon": "fa-triangle-exclamation"},
    "CRITICO": {"className": "critical", "icon": "fa-radiation"},
}

DEFAULT_EVENT_LEVELS = {
    "face_recognized": "BAJO",
    "face_unknown": "MEDIO",
    "authorized_object": "BAJO",
    "dangerous_object": "ALTO",
    "unauthorized_access": "ALTO",
}

ALERT_LEVEL_PATTERN = re.compile(
    r"(?:nivel|prioridad)\s*:\s*(BAJO|BAJA|MEDIO|MEDIA|ALTO|ALTA|CRITICO|CRÍTICO)",
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
    return str(label).strip().lower().replace("_", "-")


def _passes_ppe_confidence(label: str, confidence: float) -> bool:
    min_confidence = PPE_CLASS_CONFIDENCE.get(label, 0.55)
    return confidence >= min_confidence


def _get_supported_required_ppe_items(names) -> tuple[str, ...]:
    model_labels = {
        _normalize_ppe_label(label)
        for label in (names.values() if hasattr(names, "values") else names)
    }
    return tuple(
        item
        for item in PPE_REQUIRED_ITEMS
        if item in model_labels or f"no-{item}" in model_labels
    )


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
    return f"{camera_id}:{grid_x}:{grid_y}:{violation_type}:{_safe_event_key(base_msg)}"


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


def _format_duration(seconds):
    seconds = max(0, int(seconds))
    minutes, remaining_seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)

    if hours:
        return f"{hours}h {minutes}m {remaining_seconds}s"
    if minutes:
        return f"{minutes}m {remaining_seconds}s"
    return f"{remaining_seconds}s"


def _track_object_duration(memory, label, now):
    entry = memory.get(label)
    if entry is None:
        entry = {"first_seen": now, "last_seen": now}
        memory[label] = entry
    else:
        entry["last_seen"] = now
    return now - entry["first_seen"]


def _prune_object_presence(memory, observed_labels, now, ttl_seconds=4.0):
    for label in list(memory.keys()):
        last_seen = memory[label].get("last_seen", 0.0)
        if label not in observed_labels or now - last_seen > ttl_seconds:
            del memory[label]


def _match_identity_to_person_box(person_box, detected_faces):
    """
    Relaciona la persona detectada por PPE con un rostro reconocido.
    Primero intenta por solapamiento. Luego intenta por centro del rostro.
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
    Mantiene en memoria rostros recientes, aunque face_recognition no los vea en un frame específico.
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
            dist = (dx**2 + dy**2) ** 0.5
            if dist < FACE_MATCH_DISTANCE:
                already_matched = True
                break

        if not already_matched:
            merged_faces.append(old_face)

    return merged_faces


def _get_recent_identity(detected_faces, now):
    """
    Fallback para cuando PPE no logra asociar por caja.
    Si hay una persona en pantalla y recientemente se reconoció un rostro, usa esa identidad.
    """
    recent_faces = [
        face
        for face in detected_faces
        if now - face.get("last_seen", 0.0) <= FACE_MEMORY_SECONDS
    ]

    if not recent_faces:
        return None

    authorized_faces = [face for face in recent_faces if face.get("is_authorized")]
    if authorized_faces:
        return max(authorized_faces, key=lambda face: face.get("last_seen", 0.0))

    return max(recent_faces, key=lambda face: face.get("last_seen", 0.0))


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


def _log_line(message: str, key: str | None = None, throttle_sec: float = 0.0) -> None:
    global _LOG_SEQ

    now = time.monotonic()
    if key and throttle_sec > 0:
        last = _LAST_LOG_TS.get(key, 0.0)
        if (now - last) < throttle_sec:
            return
        _LAST_LOG_TS[key] = now

    ts = timezone.localtime().strftime("%H:%M:%S")
    with _LOG_LOCK:
        _LOG_SEQ += 1
        _LIVE_LOG.append({"id": _LOG_SEQ, "ts": ts, "msg": message})


def live_status(request):
    """Devuelve logs nuevos usando ?after="""
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


def _load_face_recognition():
    with _IMPORT_CACHE_LOCK:
        if _FACE_RECOGNITION_CACHE["attempted"]:
            return _FACE_RECOGNITION_CACHE["module"]

        face_recognition = _safe_import_face_recognition()
        _FACE_RECOGNITION_CACHE["module"] = face_recognition
        _FACE_RECOGNITION_CACHE["attempted"] = True

    if face_recognition is None:
        _log_line(
            "face_recognition no disponible: usando Haar Cascade",
            key="face_rec_missing",
            throttle_sec=10,
        )
    else:
        _log_line("face_recognition cargado", key="face_rec_loaded", throttle_sec=10)

    return face_recognition


# =========================
# YOLOv3-tiny (OpenCV DNN)
# =========================
YOLO_CONFIG = {
    "weights": os.path.join(settings.BASE_DIR, "camera", "yolov3-tiny.weights"),
    "cfg": os.path.join(settings.BASE_DIR, "camera", "yolov3-tiny.cfg"),
    "classes": os.path.join(settings.BASE_DIR, "camera", "coco.names"),
}

_YOLO_CACHE = {"net": None, "classes": None}
_YOLO_LOAD_LOCK = Lock()
_YOLO_INFERENCE_LOCK = Lock()


def _load_yolo():
    with _YOLO_LOAD_LOCK:
        return _load_yolo_locked()


def _load_yolo_locked():
    if _YOLO_CACHE["net"] is not None and _YOLO_CACHE["classes"] is not None:
        return _YOLO_CACHE["net"], _YOLO_CACHE["classes"]

    cv2_module = _safe_import_cv2()
    if cv2_module is None:
        _log_line("OpenCV no disponible: YOLO deshabilitado", key="cv2_missing", throttle_sec=10)
        return None, None

    if not (
        os.path.exists(YOLO_CONFIG["weights"])
        and os.path.exists(YOLO_CONFIG["cfg"])
        and os.path.exists(YOLO_CONFIG["classes"])
    ):
        _log_line("Archivos YOLO no encontrados (weights/cfg/classes)", key="yolo_files_missing", throttle_sec=10)
        return None, None

    try:
        net = cv2_module.dnn.readNet(YOLO_CONFIG["weights"], YOLO_CONFIG["cfg"])
        with open(YOLO_CONFIG["classes"], "r", encoding="utf-8") as f:
            classes = [line.strip() for line in f.readlines()]

        _YOLO_CACHE["net"] = net
        _YOLO_CACHE["classes"] = classes
        _log_line("✅ YOLO cargado", key="yolo_loaded", throttle_sec=10)
        return net, classes
    except Exception as e:
        _log_line(f"❌ Error cargando YOLO: {e}", key="yolo_load_err", throttle_sec=10)
        return None, None


# =========================
# PPE (Ultralytics)
# =========================
_PPE_CACHE = {"model": None}
_PPE_LOAD_LOCK = Lock()
_PPE_INFERENCE_LOCK = Lock()


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
        _log_line(f"❌ No existe ppe.pt en: {model_path}", key="ppe_file_missing", throttle_sec=10)
        return None

    try:
        model = YOLO(model_path)
        _PPE_CACHE["model"] = model
        _log_line("✅ PPE model cargado", key="ppe_loaded", throttle_sec=10)
        return model
    except Exception as e:
        _log_line(f"❌ Error cargando PPE model: {e}", key="ppe_load_err", throttle_sec=10)
        return None


# =========================
# Modelo personalizado opcional para objetos de riesgo
# =========================
_RISK_OBJECT_CACHE = {"model": None, "attempted": False}
_RISK_OBJECT_LOAD_LOCK = Lock()
_RISK_OBJECT_INFERENCE_LOCK = Lock()


def _load_risk_object_model():
    """
    Si existe camera/risk_objects.pt, lo carga.
    Este modelo personalizado permitiría detectar: estilete, aceite, madera, cemento de contacto,
    quita grasa, pintura, pistola de silicona, alcohol, etc.
    Si no existe, el sistema sigue trabajando con YOLOv3-tiny + COCO.
    """
    with _RISK_OBJECT_LOAD_LOCK:
        if _RISK_OBJECT_CACHE["attempted"]:
            return _RISK_OBJECT_CACHE["model"]

        _RISK_OBJECT_CACHE["attempted"] = True

        YOLO = _safe_import_ultralytics()
        if YOLO is None:
            _log_line("Ultralytics no disponible: modelo personalizado de objetos deshabilitado", key="risk_ultra_missing", throttle_sec=10)
            return None

        model_path = os.path.join(settings.BASE_DIR, "camera", "risk_objects.pt")
        if not os.path.exists(model_path):
            _log_line("Modelo personalizado risk_objects.pt no encontrado. Se usará COCO/YOLOv3-tiny.", key="risk_model_missing", throttle_sec=10)
            return None

        try:
            model = YOLO(model_path)
            _RISK_OBJECT_CACHE["model"] = model
            _log_line("✅ Modelo personalizado risk_objects.pt cargado", key="risk_model_loaded", throttle_sec=10)
            return model
        except Exception as e:
            _log_line(f"❌ Error cargando risk_objects.pt: {e}", key="risk_model_load_err", throttle_sec=10)
            return None


_MODEL_PRELOAD_LOCK = Lock()
_MODEL_PRELOAD_DONE = Event()
_MODEL_PRELOAD_STARTED = False


def _preload_camera_models_task():
    try:
        _safe_import_cv2()
        _safe_import_numpy()
        _load_yolo()
        _load_ppe_model()
        _load_risk_object_model()
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
        return None, None, None, None, None

    return (
        _YOLO_CACHE["net"],
        _YOLO_CACHE["classes"],
        _PPE_CACHE["model"],
        _FACE_RECOGNITION_CACHE["module"],
        _RISK_OBJECT_CACHE["model"],
    )


def _save_detection_event_from_rule(
    *,
    rule,
    confidence,
    frame,
    camera,
    duration_seconds=None,
    event_key_prefix="object",
):
    if not should_generate_detection_event(rule, confidence, duration_seconds=duration_seconds):
        return None

    event_key = (
        f"{event_key_prefix}_{rule.event_type}_camera_{camera.id}_"
        f"{_safe_event_key(rule.model_label)}"
    )

    if not can_save_event(event_key, seconds=rule.cooldown_seconds):
        return None

    details = build_detection_details(
        rule=rule,
        confidence=confidence,
        duration_seconds=duration_seconds,
    )

    try:
        event = create_security_event(
            event_type=rule.event_type,
            details=details,
            frame=frame.copy(),
            user=None,
            camera=camera,
            epp_correcto=False,
        )

        if event:
            event.category = rule.category
            event.object_label = rule.model_label
            event.risk_level = rule.risk_level
            event.confidence = confidence
            event.duration_seconds = int(duration_seconds or 0)
            event.should_alert = rule.should_alert
            event.save(
                update_fields=[
                    "category",
                    "object_label",
                    "risk_level",
                    "confidence",
                    "duration_seconds",
                    "should_alert",
                ]
            )

        return event
    except Exception as e:
        _log_line(
            f"❌ Error guardando detección {rule.model_label}: {e}",
            key=f"db_{camera.id}_{rule.event_type}_{_safe_event_key(rule.model_label)}_err",
            throttle_sec=5,
        )
        return None


def _draw_detection(frame, cv2_module, x1, y1, x2, y2, text, color):
    cv2_module.rectangle(frame, (x1, y1), (x2, y2), color, 2)
    cv2_module.putText(
        frame,
        text,
        (x1, max(y1 - 10, 20)),
        cv2_module.FONT_HERSHEY_SIMPLEX,
        0.6,
        color,
        2,
    )


# =========================
# Frames / pipeline principal
# =========================
def _run_camera_pipeline(camera: Camera, target_fps: int = 10, emit_jpeg=None, should_stop=None):
    cv2_module = _safe_import_cv2()
    np = _safe_import_numpy()

    if cv2_module is None or np is None:
        _log_line("❌ Falta cv2 o numpy", key="deps_missing", throttle_sec=5)
        return

    preload_camera_models(async_load=True)

    net = None
    coco_classes = None
    ppe_model = None
    face_rec = None
    risk_object_model = None
    models_attached = False

    camera_source = camera.get_video_source()
    camera_name = camera.nombre

    if isinstance(camera_source, int):
        cap = cv2_module.VideoCapture(camera_source, cv2_module.CAP_DSHOW)
    else:
        cap = cv2_module.VideoCapture(camera_source)

    if not cap.isOpened():
        _log_line(f"❌ No se pudo abrir la cámara: {camera_name}", key=f"cam_fail_{camera.id}", throttle_sec=10)
        return

    cap.set(cv2_module.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2_module.CAP_PROP_FRAME_HEIGHT, 480)

    face_cascade = cv2_module.CascadeClassifier(
        cv2_module.data.haarcascades + "haarcascade_frontalface_default.xml"
    )

    last_face_db_sync = 0.0
    known_face_encodings = []
    known_face_metadata = []
    current_faces = []
    last_detected_faces = []
    ppe_violation_memory = {}
    object_presence_memory = {}
    frame_counter = 0

    target_fps = max(1, min(int(target_fps), 30))
    frame_interval = 1.0 / float(target_fps)
    next_frame_at = time.monotonic()

    _log_line(
        f"Streaming iniciado: {camera_name} (fps={target_fps})",
        key=f"stream_start_{camera.id}",
        throttle_sec=2,
    )

    try:
        while True:
            if should_stop is not None and should_stop():
                break

            now = time.monotonic()

            if not models_attached and _MODEL_PRELOAD_DONE.is_set():
                net, coco_classes, ppe_model, face_rec, risk_object_model = _attach_preloaded_models()
                models_attached = True

                if net is not None or ppe_model is not None or face_rec is not None or risk_object_model is not None:
                    _log_line(
                        f"Modelos activos para {camera_name}",
                        key=f"models_ready_{camera.id}",
                        throttle_sec=10,
                    )

            if now < next_frame_at:
                time.sleep(next_frame_at - now)
            next_frame_at = max(next_frame_at + frame_interval, time.monotonic() + 0.001)

            ok, frame = cap.read()
            if not ok:
                _log_line(
                    f"❌ No se pudo leer frame de {camera_name}",
                    key=f"frame_fail_{camera.id}",
                    throttle_sec=5,
                )
                break

            frame_counter += 1
            small_frame = cv2_module.resize(frame, (320, 240))

            # =========================
            # Detección / reconocimiento facial
            # =========================
            if face_rec is not None:
                if frame_counter % 6 == 0:
                    if now - last_face_db_sync > 10.0:
                        known_face_encodings = []
                        known_face_metadata = []

                        try:
                            close_old_connections()
                            for person in AuthorizedPerson.objects.filter(is_active=True):
                                try:
                                    enc = json.loads(person.face_encoding)
                                    known_face_encodings.append(np.array(enc))
                                    known_face_metadata.append({"name": person.get_full_name(), "person": person})
                                except Exception as e:
                                    print(f"Error parsing encoding for {person}: {e}")
                        except Exception as e:
                            print(f"Error loading authorized persons: {e}")

                        last_face_db_sync = now

                    rgb_small = cv2_module.cvtColor(small_frame, cv2_module.COLOR_BGR2RGB)
                    face_locations = face_rec.face_locations(rgb_small)
                    face_encodings = face_rec.face_encodings(rgb_small, face_locations)
                    new_detected_faces = []
                    current_faces = []

                    for face_loc, face_encoding in zip(face_locations, face_encodings):
                        top, right, bottom, left = face_loc
                        cx = (left + right) // 2
                        cy = (top + bottom) // 2
                        x1 = left * 2
                        y1 = top * 2
                        x2 = right * 2
                        y2 = bottom * 2

                        tracked_face = None
                        best_dist = 999.0
                        for f in last_detected_faces:
                            dist = np.sqrt((cx - f["center"][0]) ** 2 + (cy - f["center"][1]) ** 2)
                            if dist < 50.0 and dist < best_dist:
                                best_dist = dist
                                tracked_face = f

                        name = "Desconocido"
                        is_authorized = False
                        person_obj = None

                        if known_face_encodings:
                            matches = face_rec.compare_faces(known_face_encodings, face_encoding, tolerance=0.6)
                            if True in matches:
                                face_distances = face_rec.face_distance(known_face_encodings, face_encoding)
                                best_match_idx = np.argmin(face_distances)
                                if matches[best_match_idx]:
                                    is_authorized = True
                                    person_obj = known_face_metadata[best_match_idx]["person"]
                                    name = known_face_metadata[best_match_idx]["name"]

                        if not is_authorized and tracked_face is not None:
                            if tracked_face["is_authorized"] and (now - tracked_face["last_authorized_ts"] < 4.0):
                                is_authorized = True
                                person_obj = tracked_face["person_obj"]
                                name = tracked_face["name"]

                        last_auth_ts = tracked_face["last_authorized_ts"] if (tracked_face and tracked_face["is_authorized"]) else 0.0
                        if is_authorized:
                            last_auth_ts = now

                        new_detected_faces.append(
                            {
                                "center": (cx, cy),
                                "name": name,
                                "is_authorized": is_authorized,
                                "person_obj": person_obj,
                                "last_authorized_ts": last_auth_ts,
                                "coords": (x1, y1, x2, y2),
                                "last_seen": now,
                            }
                        )

                        if is_authorized:
                            current_faces.append((x1, y1, x2, y2, f"Autorizado: {name}", (0, 255, 0)))
                            _log_line(
                                f"FACE [{camera_name}]: ✅ Autorizado: {name}",
                                key=f"face_rec_log_{camera.id}_{person_obj.id}",
                                throttle_sec=15.0,
                            )

                            event_key = f"face_rec_event_{camera.id}_{person_obj.id}"
                            if can_save_event(event_key, seconds=30):
                                try:
                                    event = create_security_event(
                                        event_type="face_recognized",
                                        details=f"Rostro autorizado detectado: {name} (Cargo: {person_obj.cargo}) | Nivel: BAJO",
                                        frame=frame.copy(),
                                        camera=camera,
                                        authorized_person=person_obj,
                                        epp_correcto=True,
                                    )
                                    if event:
                                        event.risk_level = "BAJO"
                                        event.should_alert = False
                                        event.save(update_fields=["risk_level", "should_alert"])
                                except Exception as e:
                                    print(f"Error saving recognized event: {e}")
                        else:
                            current_faces.append((x1, y1, x2, y2, "NO AUTORIZADO", (0, 0, 255)))
                            _log_line(
                                f"FACE [{camera_name}]: ❌ Persona no autorizada detectada",
                                key=f"face_unauth_log_{camera.id}",
                                throttle_sec=15.0,
                            )

                            event_key = f"unauthorized_face_event_{camera.id}"
                            if can_save_event(event_key, seconds=30):
                                try:
                                    event = create_security_event(
                                        event_type="unauthorized_access",
                                        details="Persona no autorizada detectada en el área monitoreada | Nivel: ALTO",
                                        frame=frame.copy(),
                                        camera=camera,
                                        epp_correcto=False,
                                    )
                                    if event:
                                        event.category = "unauthorized"
                                        event.risk_level = "ALTO"
                                        event.should_alert = True
                                        event.save(update_fields=["category", "risk_level", "should_alert"])
                                except Exception as e:
                                    print(f"Error saving unauthorized event: {e}")

                    last_detected_faces = _merge_recent_faces(
                        old_faces=last_detected_faces,
                        new_faces=new_detected_faces,
                        now=now,
                    )
            else:
                if frame_counter % 3 == 0:
                    gray = cv2_module.cvtColor(small_frame, cv2_module.COLOR_BGR2GRAY)
                    faces = face_cascade.detectMultiScale(gray, 1.1, 4)
                    if len(faces) > 0:
                        _log_line(
                            f"FACE [{camera_name}]: {len(faces)} rostro(s)",
                            key=f"face_count_{camera.id}",
                            throttle_sec=0.8,
                        )

                    current_faces = []
                    for (x, y, w, h) in faces:
                        x1 = x * 2
                        y1 = y * 2
                        x2 = (x + w) * 2
                        y2 = (y + h) * 2
                        current_faces.append((x1, y1, x2, y2, "Rostro", (255, 0, 0)))

            for x1, y1, x2, y2, label, color in current_faces:
                _draw_detection(frame, cv2_module, x1, y1, x2, y2, label, color)

            # =========================
            # YOLOv3 / COCO para objetos generales
            # =========================
            if frame_counter % 6 == 0 and net is not None and coco_classes is not None:
                monitored_labels = get_monitored_model_labels()
                blob = cv2_module.dnn.blobFromImage(
                    frame,
                    1 / 255.0,
                    (416, 416),
                    swapRB=True,
                    crop=False,
                )

                with _YOLO_INFERENCE_LOCK:
                    net.setInput(blob)
                    outs = net.forward(net.getUnconnectedOutLayersNames())

                height, width = frame.shape[:2]
                boxes, confs, class_ids = [], [], []
                observed_object_keys = set()

                for out in outs:
                    for det in out:
                        scores = det[5:]
                        class_id = int(np.argmax(scores))
                        confidence = float(scores[class_id])

                        if confidence <= DEFAULT_OBJECT_CONFIDENCE:
                            continue

                        label = coco_classes[class_id].lower().strip()
                        if label not in monitored_labels:
                            continue

                        rule = get_detection_rule(label)
                        if rule is None:
                            continue

                        if confidence < rule.min_confidence:
                            continue

                        cx = int(det[0] * width)
                        cy = int(det[1] * height)
                        w = int(det[2] * width)
                        h = int(det[3] * height)
                        x = int(cx - w / 2)
                        y = int(cy - h / 2)
                        boxes.append([x, y, w, h])
                        confs.append(confidence)
                        class_ids.append(class_id)

                if boxes:
                    idxs = cv2_module.dnn.NMSBoxes(boxes, confs, 0.20, 0.4)
                    idxs = idxs.flatten().tolist() if hasattr(idxs, "flatten") else list(idxs)

                    for i in idxs:
                        x, y, w, h = boxes[i]
                        label = coco_classes[class_ids[i]].lower().strip()
                        conf = confs[i]
                        rule = get_detection_rule(label)

                        if rule is None:
                            continue

                        object_key = f"{camera.id}_{rule.model_label}"
                        observed_object_keys.add(object_key)

                        duration_seconds = None
                        duration_text = ""

                        if rule.requires_duration:
                            duration_seconds = _track_object_duration(object_presence_memory, object_key, now)
                            duration_text = f" | {_format_duration(duration_seconds)}"

                        color = get_rule_color(rule)
                        display_text = f"{rule.name}: {conf:.2f} | {rule.risk_level}{duration_text}"
                        _draw_detection(frame, cv2_module, x, y, x + w, y + h, display_text, color)

                        _log_line(
                            f"OBJ [{camera_name}]: {rule.name} ({conf:.2f}) | Nivel {rule.risk_level}{duration_text}",
                            key=f"obj_{camera.id}_{_safe_event_key(rule.model_label)}",
                            throttle_sec=0.25,
                        )

                        event = _save_detection_event_from_rule(
                            rule=rule,
                            confidence=conf,
                            frame=frame,
                            camera=camera,
                            duration_seconds=duration_seconds,
                            event_key_prefix="coco",
                        )

                        if event:
                            if rule.should_alert:
                                _log_line(
                                    f"ALERTA [{camera_name}]: {rule.name} | Nivel {rule.risk_level}",
                                    key=f"alert_{camera.id}_{_safe_event_key(rule.model_label)}",
                                    throttle_sec=2,
                                )
                            else:
                                _log_line(
                                    f"Registro sin alerta [{camera_name}]: {rule.name} | Nivel {rule.risk_level}",
                                    key=f"register_{camera.id}_{_safe_event_key(rule.model_label)}",
                                    throttle_sec=2,
                                )

                _prune_object_presence(object_presence_memory, observed_object_keys, now)

            # =========================
            # Modelo personalizado opcional para objetos de riesgo
            # =========================
            if frame_counter % 12 == 0 and risk_object_model is not None:
                try:
                    with _RISK_OBJECT_INFERENCE_LOCK:
                        result = risk_object_model(frame, verbose=False, imgsz=640, conf=0.25)[0]

                    names = result.names
                    observed_risk_keys = set()

                    for b in result.boxes:
                        cls_id = int(b.cls[0])
                        label = str(names.get(cls_id, cls_id)).lower().strip().replace("_", "_")
                        conf = float(b.conf[0])
                        rule = get_detection_rule(label)

                        if rule is None or conf < rule.min_confidence:
                            continue

                        x1, y1, x2, y2 = map(int, b.xyxy[0].tolist())
                        object_key = f"risk_{camera.id}_{rule.model_label}"
                        observed_risk_keys.add(object_key)

                        duration_seconds = None
                        duration_text = ""
                        if rule.requires_duration:
                            duration_seconds = _track_object_duration(object_presence_memory, object_key, now)
                            duration_text = f" | {_format_duration(duration_seconds)}"

                        color = get_rule_color(rule)
                        display_text = f"{rule.name}: {conf:.2f} | {rule.risk_level}{duration_text}"
                        _draw_detection(frame, cv2_module, x1, y1, x2, y2, display_text, color)

                        event = _save_detection_event_from_rule(
                            rule=rule,
                            confidence=conf,
                            frame=frame,
                            camera=camera,
                            duration_seconds=duration_seconds,
                            event_key_prefix="risk_model",
                        )

                        if event:
                            log_prefix = "ALERTA" if rule.should_alert else "Registro sin alerta"
                            _log_line(
                                f"{log_prefix} [{camera_name}]: {rule.name} | Nivel {rule.risk_level}",
                                key=f"risk_{camera.id}_{_safe_event_key(rule.model_label)}",
                                throttle_sec=2,
                            )

                    _prune_object_presence(object_presence_memory, observed_risk_keys, now)
                except Exception as e:
                    _log_line(
                        f"❌ Error modelo personalizado [{camera_name}]: {e}",
                        key=f"risk_detect_err_{camera.id}",
                        throttle_sec=5,
                    )

            # =========================
            # PPE
            # =========================
            if ppe_model is not None and frame_counter % 15 == 0:
                try:
                    with _PPE_INFERENCE_LOCK:
                        res = ppe_model(
                            frame,
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
                        x1, y1, x2, y2 = map(int, b.xyxy[0].tolist())

                        if not _passes_ppe_confidence(label, conf):
                            continue

                        if label == "person":
                            persons.append((x1, y1, x2, y2, conf))
                        else:
                            items.append((label, conf, x1, y1, x2, y2))

                    for (px1, py1, px2, py2, _person_conf) in persons:
                        identity = _match_identity_to_person_box(
                            (px1, py1, px2, py2),
                            last_detected_faces,
                        )

                        if identity is None and len(persons) == 1:
                            identity = _get_recent_identity(last_detected_faces, now)

                        person_name, auth_status, authorized_person = _get_identity_text(identity)

                        present = set()
                        negatives = set()

                        for (label, conf, x1, y1, x2, y2) in items:
                            if _is_ppe_item_inside_person((px1, py1, px2, py2), (x1, y1, x2, y2)):
                                present.add(label)
                                if label.startswith("no-"):
                                    negatives.add(label)

                        if negatives:
                            base_msg = "⚠ Indumentaria incorrecta: " + ", ".join(sorted([x.upper() for x in negatives]))
                            msg = f"{base_msg} | Nivel: ALTO | Persona: {person_name} | Estado: {auth_status}"
                            violation_key = _ppe_violation_key(camera.id, (px1, py1, px2, py2), "negative", base_msg)
                            observed_ppe_violation_keys.add(violation_key)
                            is_confirmed, confirmation_count = _track_ppe_violation(ppe_violation_memory, violation_key, now)

                            if not is_confirmed:
                                _log_line(
                                    f"PPE [{camera_name}]: verificando {base_msg} ({confirmation_count}/{PPE_CONFIRMATION_FRAMES})",
                                    key=f"ppe_pending_neg_{camera.id}_{_safe_event_key(base_msg)}",
                                    throttle_sec=1.2,
                                )
                                _draw_detection(frame, cv2_module, px1, py1, px2, py2, "Verificando EPP...", (0, 165, 255))
                                continue

                            _log_line(f"PPE [{camera_name}]: {msg}", key=f"ppe_neg_{camera.id}", throttle_sec=0.4)
                            event_key = f"ppe_incorrect_{camera.id}_{_safe_event_key(person_name)}_{_safe_event_key(base_msg)}"
                            if can_save_event(event_key, seconds=25):
                                event = create_security_event(
                                    event_type="unauthorized_access",
                                    details=msg,
                                    frame=frame.copy(),
                                    camera=camera,
                                    authorized_person=authorized_person,
                                    epp_correcto=False,
                                )
                                if event:
                                    event.category = "ppe"
                                    event.risk_level = "ALTO"
                                    event.should_alert = True
                                    event.save(update_fields=["category", "risk_level", "should_alert"])

                            _draw_detection(frame, cv2_module, px1, py1, px2, py2, msg, (0, 255, 255))
                            continue

                        missing = []
                        for required_item in supported_required_ppe_items:
                            if required_item not in present:
                                missing.append(required_item)

                        if missing:
                            base_msg = f"⚠ Falta EPP: {', '.join(missing)}"
                            msg = f"{base_msg} | Nivel: ALTO | Persona: {person_name} | Estado: {auth_status}"
                            violation_key = _ppe_violation_key(camera.id, (px1, py1, px2, py2), "missing", base_msg)
                            observed_ppe_violation_keys.add(violation_key)
                            is_confirmed, confirmation_count = _track_ppe_violation(ppe_violation_memory, violation_key, now)

                            if not is_confirmed:
                                _log_line(
                                    f"PPE [{camera_name}]: verificando {base_msg} ({confirmation_count}/{PPE_CONFIRMATION_FRAMES})",
                                    key=f"ppe_pending_missing_{camera.id}_{_safe_event_key(base_msg)}",
                                    throttle_sec=1.2,
                                )
                                _draw_detection(frame, cv2_module, px1, py1, px2, py2, "Verificando EPP...", (0, 165, 255))
                                continue

                            _log_line(f"PPE [{camera_name}]: {msg}", key=f"ppe_missing_{camera.id}", throttle_sec=0.4)
                            event_key = f"ppe_missing_{camera.id}_{_safe_event_key(person_name)}_{_safe_event_key(base_msg)}"
                            if can_save_event(event_key, seconds=25):
                                event = create_security_event(
                                    event_type="unauthorized_access",
                                    details=msg,
                                    frame=frame.copy(),
                                    camera=camera,
                                    authorized_person=authorized_person,
                                    epp_correcto=False,
                                )
                                if event:
                                    event.category = "ppe"
                                    event.risk_level = "ALTO"
                                    event.should_alert = True
                                    event.save(update_fields=["category", "risk_level", "should_alert"])

                            _draw_detection(frame, cv2_module, px1, py1, px2, py2, msg, (0, 255, 255))
                        else:
                            _log_line(f"PPE [{camera_name}]: ✅ EPP OK", key=f"ppe_ok_{camera.id}", throttle_sec=1.2)
                            _draw_detection(frame, cv2_module, px1, py1, px2, py2, "EPP OK", (0, 255, 0))

                    _prune_ppe_violations(ppe_violation_memory, observed_ppe_violation_keys, now)
                except Exception as e:
                    _log_line(
                        f"❌ Error PPE detect [{camera_name}]: {e}",
                        key=f"ppe_detect_err_{camera.id}",
                        throttle_sec=5,
                    )

            ret, buffer = cv2_module.imencode(".jpg", frame, [int(cv2_module.IMWRITE_JPEG_QUALITY), 70])
            if not ret:
                continue

            if emit_jpeg is not None:
                emit_jpeg(buffer.tobytes())
    finally:
        cap.release()
        _log_line(f"Streaming detenido: {camera_name}", key=f"stream_stop_{camera.id}", throttle_sec=2)


CAMERA_WORKER_IDLE_SECONDS = 45.0
CAMERA_SIGNAL_TIMEOUT_SECONDS = 4.0
_CAMERA_WORKERS: dict[int, "CameraStreamWorker"] = {}
_CAMERA_WORKERS_LOCK = Lock()


class CameraStreamWorker:
    def __init__(self, camera: Camera, target_fps: int):
        self.camera = camera
        self.camera_id = camera.id
        self.camera_source = camera.source
        self.camera_name = camera.nombre
        self.target_fps = max(1, min(int(target_fps), 30))
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

    def stop(self):
        self._stop_event.set()

    def should_stop(self) -> bool:
        if self._stop_event.is_set():
            return True
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


def _get_or_start_camera_worker(camera: Camera, target_fps: int) -> CameraStreamWorker:
    with _CAMERA_WORKERS_LOCK:
        worker = _CAMERA_WORKERS.get(camera.id)
        if worker is None or not worker.is_alive() or not worker.matches(camera):
            if worker is not None:
                worker.stop()
            worker = CameraStreamWorker(camera=camera, target_fps=target_fps)
            _CAMERA_WORKERS[camera.id] = worker
            worker.start()

        worker.touch()
        return worker


def gen_frames(camera: Camera, target_fps: int = 10):
    worker = _get_or_start_camera_worker(camera, target_fps)
    target_fps = max(1, min(int(target_fps), 30))
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
    try:
        return int(request.GET.get("fps", "5"))
    except ValueError:
        return 5


@login_required(login_url="/login/")
def video_feed(request, camera_id):
    if not is_admin_user(request.user):
        return _json_forbidden("Solo un administrador puede visualizar el stream de camara.")

    cv2_module = _safe_import_cv2()
    if cv2_module is None:
        return JsonResponse({"success": False, "message": "OpenCV no está instalado."}, status=400)

    camera = get_object_or_404(Camera, id=camera_id, is_active=True)
    fps = _get_request_fps(request)
    return StreamingHttpResponse(
        gen_frames(camera=camera, target_fps=fps),
        content_type="multipart/x-mixed-replace;boundary=frame",
    )


@login_required(login_url="/login/")
def video_feed_default(request):
    if not is_admin_user(request.user):
        return _json_forbidden("Solo un administrador puede visualizar el stream de camara.")

    cv2_module = _safe_import_cv2()
    if cv2_module is None:
        return JsonResponse({"success": False, "message": "OpenCV no está instalado."}, status=400)

    camera = Camera.objects.filter(is_active=True).order_by("id").first()
    if camera is None:
        return JsonResponse({"success": False, "message": "No hay cámaras activas configuradas."}, status=404)

    fps = _get_request_fps(request)
    return StreamingHttpResponse(
        gen_frames(camera=camera, target_fps=fps),
        content_type="multipart/x-mixed-replace;boundary=frame",
    )


@login_required(login_url="/login/")
def camera_status(request, camera_id):
    if not is_admin_user(request.user):
        return _json_forbidden("Solo un administrador puede consultar el estado de camara.")

    camera = get_object_or_404(Camera, id=camera_id)

    if not camera.is_active:
        return JsonResponse(
            {
                "success": True,
                "camera_id": camera.id,
                "name": camera.nombre,
                "status": "inactive",
                "label": "Inactiva",
                "message": "La cámara está desactivada en el sistema.",
                "tone": "secondary",
            }
        )

    with _CAMERA_WORKERS_LOCK:
        worker = _CAMERA_WORKERS.get(camera.id)

    if worker is None:
        return JsonResponse(
            {
                "success": True,
                "camera_id": camera.id,
                "name": camera.nombre,
                "status": "no_signal",
                "label": "Sin señal",
                "message": "La cámara está habilitada, pero todavía no se ha iniciado el flujo.",
                "tone": "warning",
            }
        )

    if worker.has_stopped():
        return JsonResponse(
            {
                "success": True,
                "camera_id": camera.id,
                "name": camera.nombre,
                "status": "no_signal",
                "label": "Sin señal",
                "message": "El flujo de la cámara está detenido o no pudo iniciarse.",
                "tone": "danger",
            }
        )

    latest_at = worker.get_latest_at()
    if not latest_at:
        return JsonResponse(
            {
                "success": True,
                "camera_id": camera.id,
                "name": camera.nombre,
                "status": "no_signal",
                "label": "Sin señal",
                "message": "La cámara está habilitada, pero no está entregando imagen.",
                "tone": "warning",
            }
        )

    seconds_without_signal = time.monotonic() - latest_at
    if seconds_without_signal > CAMERA_SIGNAL_TIMEOUT_SECONDS:
        return JsonResponse(
            {
                "success": True,
                "camera_id": camera.id,
                "name": camera.nombre,
                "status": "no_signal",
                "label": "Sin señal",
                "message": f"No se reciben frames desde hace {seconds_without_signal:.1f} segundos.",
                "tone": "danger",
                "seconds_without_signal": round(seconds_without_signal, 1),
            }
        )

    if not worker.has_fresh_frame():
        return JsonResponse(
            {
                "success": True,
                "camera_id": camera.id,
                "name": camera.nombre,
                "status": "no_signal",
                "label": "Sin señal",
                "message": "No hay un frame reciente disponible.",
                "tone": "danger",
            }
        )

    return JsonResponse(
        {
            "success": True,
            "camera_id": camera.id,
            "name": camera.nombre,
            "status": "active",
            "label": "Activa",
            "message": "La cámara está activa y transmitiendo señal.",
            "tone": "success",
            "seconds_without_signal": round(seconds_without_signal, 1),
        }
    )


@csrf_exempt
@login_required(login_url="/login/")
def register_face(request):
    if not is_admin_user(request.user):
        return _json_forbidden("Solo un administrador puede registrar rostros autorizados.")

    if request.method != "POST":
        return JsonResponse({"success": False, "message": "Método no permitido."}, status=405)

    try:
        face_recognition = _safe_import_face_recognition()
        if face_recognition is None:
            return JsonResponse(
                {"success": False, "message": "La librería face_recognition no está instalada."},
                status=400,
            )

        nombres = request.POST.get("nombres", "").strip()
        apellidos = request.POST.get("apellidos", "").strip()
        celular = request.POST.get("celular", "").strip()
        correo = request.POST.get("correo", "").strip().lower()
        cargo = request.POST.get("cargo", "").strip()
        image_file = request.FILES.get("image")

        if not nombres:
            return JsonResponse({"success": False, "message": "Ingresa los nombres de la persona."}, status=400)
        if not apellidos:
            return JsonResponse({"success": False, "message": "Ingresa los apellidos de la persona."}, status=400)
        if not correo:
            return JsonResponse({"success": False, "message": "Ingresa el correo de la persona."}, status=400)
        if not cargo:
            return JsonResponse({"success": False, "message": "Ingresa el cargo de la persona."}, status=400)
        if not image_file:
            return JsonResponse({"success": False, "message": "Selecciona una imagen del rostro."}, status=400)

        image_data = face_recognition.load_image_file(image_file)
        face_locations = face_recognition.face_locations(image_data)

        if not face_locations:
            return JsonResponse({"success": False, "message": "No se detectó ningún rostro en la imagen."}, status=400)

        if len(face_locations) > 1:
            return JsonResponse({"success": False, "message": "La imagen debe contener solo un rostro."}, status=400)

        encodings = face_recognition.face_encodings(image_data, face_locations)
        if not encodings:
            return JsonResponse({"success": False, "message": "No se pudo generar la codificación facial."}, status=400)

        encoding_json = json.dumps(encodings[0].tolist())
        image_file.seek(0)
        face_image_path = save_authorized_face_image(image_file, correo)

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
            },
        )

        action = "registrado" if created else "actualizado"
        _log_line(
            f"✅ Rostro autorizado {action}: {person.get_full_name()}",
            key=f"face_registered_{person.id}",
            throttle_sec=1.5,
        )

        return JsonResponse(
            {"success": True, "message": f"Rostro {action} correctamente para {person.get_full_name()}."}
        )
    except Exception as e:
        print("[ERROR] register_face:")
        print(traceback.format_exc())
        return JsonResponse(
            {"success": False, "message": f"Error interno al registrar el rostro: {str(e)}"},
            status=500,
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
    detection_counts = _detection_counts_for_people([event.authorized_person_id for event in events])
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
        return JsonResponse({"status": "error", "message": "Método no permitido"}, status=405)

    event = get_object_or_404(SecurityEvent, id=event_id)
    event.resolved = True
    event.managed_by = request.user
    event.managed_at = timezone.now()
    event.save(update_fields=["resolved", "managed_by", "managed_at"])

    _log_line(f"✅ Evento resuelto: {event_id}", key=f"ev_res_{event_id}", throttle_sec=0.5)
    return JsonResponse({"status": "success"})


@login_required(login_url="/login/")
def get_security_events(request):
    events = _filtered_security_events_queryset(request)
    user_authorized_person = get_authorized_person_for_user(request.user)
    scanned_events = list(events[:50])
    detection_counts = _detection_counts_for_people([event.authorized_person_id for event in scanned_events])

    events_data = [
        _event_payload(
            event,
            request_user=request.user,
            user_authorized_person=user_authorized_person,
            detection_counts=detection_counts,
        )
        for event in scanned_events
    ]
    return JsonResponse({"events": events_data})


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
    return JsonResponse(
        {
            "success": True,
            "event": _event_payload(
                event,
                request_user=request.user,
                user_authorized_person=user_authorized_person,
                detection_counts=detection_counts,
            ),
        }
    )


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
        return context


class AlertaView(LoginRequiredMixin, TemplateView):
    template_name = "alertas/alerta.html"
    login_url = "/login/"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["segment"] = "alerta"
        return context
