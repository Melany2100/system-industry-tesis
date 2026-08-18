"""Sincronización durable de eventos desde el nodo edge hacia Django cloud."""

from __future__ import annotations

import hmac
import logging
import mimetypes
import os
import re
import threading
from contextlib import ExitStack
from datetime import timedelta

import requests
from django.conf import settings
from django.core.files.storage import default_storage
from django.db import IntegrityError, close_old_connections, transaction
from django.http import JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from core_apps.camera.models import (
    AuthorizedPerson,
    Camera,
    EventSyncOutbox,
    SecurityEvent,
)


logger = logging.getLogger(__name__)
_WORKER_LOCK = threading.Lock()
_WORKER_STARTED = False
_SOURCE_KEY_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,180}$")


def _source_node_id() -> str:
    raw = str(getattr(settings, "SMRI_EDGE_NODE_ID", "edge-main"))
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "-", raw).strip("-.")
    return (normalized or "edge-main")[:120]


def enqueue_security_event(event_id: int) -> EventSyncOutbox | None:
    """Crea la fila de salida sin efectuar ninguna llamada de red."""

    if not getattr(settings, "SMRI_EDGE_ENABLED", False):
        return None

    event = SecurityEvent.objects.get(pk=event_id)
    source_key = f"{_source_node_id()}:{event.pk}"
    outbox, _ = EventSyncOutbox.objects.get_or_create(
        event=event,
        defaults={
            "source_key": source_key,
            # Deja un breve margen para que el detector complete campos
            # estructurados que actualiza inmediatamente después de crear.
            "next_attempt_at": timezone.now() + timedelta(seconds=2),
        },
    )
    return outbox


def backfill_event_outbox(limit: int = 100) -> int:
    """Reconstruye la cola para eventos locales que aún no tengan salida."""

    event_ids = list(
        SecurityEvent.objects.filter(
            source_event_key__isnull=True,
            sync_outbox__isnull=True,
        )
        .order_by("-pk")
        .values_list("pk", flat=True)[: max(1, limit)]
    )
    for event_id in reversed(event_ids):
        enqueue_security_event(event_id)
    EventSyncOutbox.objects.filter(
        event_id__in=event_ids,
        status__in=(EventSyncOutbox.STATUS_PENDING, EventSyncOutbox.STATUS_RETRY),
    ).update(next_attempt_at=timezone.now())
    return len(event_ids)


def _event_payload(event: SecurityEvent, source_key: str) -> dict[str, str]:
    report = event.informes.order_by("pk").first()
    person = event.authorized_person
    user = event.related_user
    person_name = event.get_person_name()
    person_email = ""
    if person and person.correo:
        person_email = person.correo
    elif user and user.email:
        person_email = user.email

    return {
        "source_event_key": source_key,
        "event_type": event.event_type,
        "severity": event.severity,
        "details": event.details or "",
        "timestamp": event.timestamp.isoformat(),
        "object_label": event.object_label or "",
        "category": event.category or "",
        "risk_level": event.risk_level or "",
        "confidence": "" if event.confidence is None else str(event.confidence),
        "duration_seconds": str(event.duration_seconds or 0),
        "should_alert": "1" if event.should_alert else "0",
        "camera_stream_path": event.camera.get_stream_path() if event.camera else "",
        "camera_name": event.camera.nombre if event.camera else "",
        "person_name": person_name,
        "person_email": person_email,
        "epp_correcto": "1" if report and report.epp_correcto else "0",
    }


def _send_outbox(outbox: EventSyncOutbox) -> None:
    event = outbox.event
    headers = {
        "Authorization": f"Bearer {settings.SMRI_EVENT_SYNC_TOKEN}",
        "Idempotency-Key": outbox.source_key,
    }
    data = _event_payload(event, outbox.source_key)

    with ExitStack() as stack:
        files = None
        if event.image_path and default_storage.exists(event.image_path):
            evidence = stack.enter_context(default_storage.open(event.image_path, "rb"))
            filename = os.path.basename(event.image_path) or "evidencia.jpg"
            content_type = mimetypes.guess_type(filename)[0] or "image/jpeg"
            files = {"evidence": (filename, evidence, content_type)}

        response = requests.post(
            settings.SMRI_EVENT_SYNC_URL,
            headers=headers,
            data=data,
            files=files,
            timeout=settings.SMRI_EVENT_SYNC_TIMEOUT_SECONDS,
        )
        if response.status_code < 200 or response.status_code >= 300:
            body = (response.text or "").replace(settings.SMRI_EVENT_SYNC_TOKEN, "***")
            raise RuntimeError(f"HTTP {response.status_code}: {body[:500]}")


def _mark_synced(outbox: EventSyncOutbox) -> None:
    EventSyncOutbox.objects.filter(pk=outbox.pk).update(
        status=EventSyncOutbox.STATUS_SYNCED,
        attempts=outbox.attempts + 1,
        next_attempt_at=timezone.now(),
        last_error="",
        synced_at=timezone.now(),
        updated_at=timezone.now(),
    )


def _mark_retry(outbox: EventSyncOutbox, exc: Exception) -> None:
    attempts = outbox.attempts + 1
    delay = min(300, max(5, 2 ** min(attempts, 8)))
    safe_error = str(exc).replace(settings.SMRI_EVENT_SYNC_TOKEN, "***")[:1000]
    EventSyncOutbox.objects.filter(pk=outbox.pk).update(
        status=EventSyncOutbox.STATUS_RETRY,
        attempts=attempts,
        next_attempt_at=timezone.now() + timedelta(seconds=delay),
        last_error=safe_error,
        updated_at=timezone.now(),
    )
    logger.warning(
        "No se pudo sincronizar %s; nuevo intento en %ss: %s",
        outbox.source_key,
        delay,
        safe_error,
    )


def process_due_events(limit: int | None = None) -> dict[str, int]:
    """Procesa un lote. Cada fallo queda programado con backoff exponencial."""

    if not settings.SMRI_EVENT_SYNC_URL or not settings.SMRI_EVENT_SYNC_TOKEN:
        return {"synced": 0, "failed": 0}

    batch_size = limit or settings.SMRI_EVENT_SYNC_BATCH_SIZE
    due = list(
        EventSyncOutbox.objects.select_related(
            "event",
            "event__camera",
            "event__authorized_person",
            "event__related_user",
        )
        .filter(
            status__in=(EventSyncOutbox.STATUS_PENDING, EventSyncOutbox.STATUS_RETRY),
            next_attempt_at__lte=timezone.now(),
        )
        .order_by("next_attempt_at", "pk")[:batch_size]
    )
    result = {"synced": 0, "failed": 0}
    for outbox in due:
        try:
            _send_outbox(outbox)
        except Exception as exc:
            _mark_retry(outbox, exc)
            result["failed"] += 1
        else:
            _mark_synced(outbox)
            result["synced"] += 1
    return result


def _worker_loop() -> None:
    while True:
        try:
            close_old_connections()
            process_due_events()
        except Exception:
            logger.exception("Fallo inesperado en el sincronizador de eventos")
        finally:
            close_old_connections()
        threading.Event().wait(settings.SMRI_EVENT_SYNC_INTERVAL_SECONDS)


def start_event_sync_worker() -> bool:
    """Inicia una sola hebra en el proceso edge que ejecuta runserver."""

    global _WORKER_STARTED
    if not getattr(settings, "SMRI_EDGE_ENABLED", False):
        return False
    if not settings.SMRI_EVENT_SYNC_URL:
        return False
    if not settings.SMRI_EVENT_SYNC_TOKEN:
        logger.error("SMRI_EVENT_SYNC_URL está configurada pero falta SMRI_EVENT_SYNC_TOKEN")
        return False

    with _WORKER_LOCK:
        if _WORKER_STARTED:
            return True
        thread = threading.Thread(
            target=_worker_loop,
            name="smri-event-sync",
            daemon=True,
        )
        thread.start()
        _WORKER_STARTED = True
    logger.info("Sincronizador de eventos edge iniciado")
    return True


def _post_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _post_int(value: str | None, default: int = 0) -> int:
    try:
        return max(0, int(value or default))
    except (TypeError, ValueError):
        return default


def _post_float(value: str | None) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def is_sync_request_authorized(request) -> bool:
    expected = settings.SMRI_EVENT_SYNC_TOKEN
    supplied = request.headers.get("Authorization", "")
    if supplied.startswith("Bearer "):
        supplied = supplied[7:]
    else:
        supplied = ""
    return bool(expected) and hmac.compare_digest(expected, supplied)


def _find_cloud_camera(stream_path: str, camera_name: str) -> Camera | None:
    camera = None
    if stream_path:
        camera = Camera.objects.filter(stream_path=stream_path).first()
    if camera is None and camera_name:
        camera = Camera.objects.filter(nombre=camera_name).first()
    return camera


def _save_uploaded_evidence(uploaded, source_key: str) -> str | None:
    if uploaded is None:
        return None
    extension = os.path.splitext(uploaded.name or "")[1].lower()
    if extension not in {".jpg", ".jpeg", ".png", ".webp"}:
        extension = ".jpg"
    safe_key = re.sub(r"[^A-Za-z0-9_.-]+", "-", source_key)[:150]
    target = timezone.localtime().strftime(
        f"security_events/synced/%Y/%m/%d/{safe_key}{extension}"
    )
    return default_storage.save(target, uploaded)


@csrf_exempt
@require_POST
def ingest_edge_event(request):
    """API privada usada por el edge; no usa sesión ni expone PostgreSQL."""

    if getattr(settings, "SMRI_NODE_ROLE", "edge") != "cloud":
        return JsonResponse({"success": False, "error": "cloud_only"}, status=404)
    if not settings.SMRI_EVENT_SYNC_TOKEN:
        return JsonResponse({"success": False, "error": "sync_not_configured"}, status=503)
    if not is_sync_request_authorized(request):
        return JsonResponse({"success": False, "error": "unauthorized"}, status=401)

    source_key = (
        request.headers.get("Idempotency-Key")
        or request.POST.get("source_event_key")
        or ""
    ).strip()
    if not _SOURCE_KEY_RE.fullmatch(source_key):
        return JsonResponse({"success": False, "error": "invalid_source_key"}, status=400)

    existing = SecurityEvent.objects.filter(source_event_key=source_key).first()
    if existing is not None:
        return JsonResponse(
            {"success": True, "duplicate": True, "event_id": existing.pk},
            status=200,
        )

    event_type = (request.POST.get("event_type") or "").strip()
    severity = (request.POST.get("severity") or "MEDIO").strip().upper()
    valid_types = {value for value, _ in SecurityEvent.EVENT_TYPES}
    valid_severities = {value for value, _ in SecurityEvent.SEVERITY_LEVELS}
    if event_type not in valid_types:
        return JsonResponse({"success": False, "error": "invalid_event_type"}, status=400)
    if severity not in valid_severities:
        severity = "MEDIO"

    stream_path = (request.POST.get("camera_stream_path") or "").strip()[:120]
    camera_name = (request.POST.get("camera_name") or "").strip()[:100]
    camera = _find_cloud_camera(stream_path, camera_name)
    person_email = (request.POST.get("person_email") or "").strip()
    authorized_person = None
    if person_email:
        authorized_person = AuthorizedPerson.objects.filter(
            correo__iexact=person_email
        ).first()

    image_path = _save_uploaded_evidence(request.FILES.get("evidence"), source_key)
    timestamp = parse_datetime((request.POST.get("timestamp") or "").strip())
    if timestamp is not None and timezone.is_naive(timestamp):
        timestamp = timezone.make_aware(timestamp, timezone.get_current_timezone())

    defaults = {
        "event_type": event_type,
        "severity": severity,
        "details": request.POST.get("details") or "Evento sincronizado desde edge.",
        "image_path": image_path,
        "object_label": (request.POST.get("object_label") or "")[:100] or None,
        "category": (request.POST.get("category") or "")[:30] or None,
        "risk_level": (request.POST.get("risk_level") or "")[:20] or None,
        "confidence": _post_float(request.POST.get("confidence")),
        "duration_seconds": _post_int(request.POST.get("duration_seconds")),
        "should_alert": _post_bool(request.POST.get("should_alert"), True),
        "camera": camera,
        "authorized_person": authorized_person,
        "email_status": "SKIPPED",
        "email_error": "Evento sincronizado; la notificación se gestiona en el nodo edge.",
    }

    try:
        with transaction.atomic():
            event, created = SecurityEvent.objects.get_or_create(
                source_event_key=source_key,
                defaults=defaults,
            )
            if not created:
                return JsonResponse(
                    {"success": True, "duplicate": True, "event_id": event.pk},
                    status=200,
                )
            if timestamp is not None:
                SecurityEvent.objects.filter(pk=event.pk).update(timestamp=timestamp)
                event.timestamp = timestamp

            from core_apps.informes.models import Informe

            Informe.objects.get_or_create(
                security_event=event,
                defaults={
                    "camara": camera.nombre if camera else camera_name or "Cámara edge",
                    "persona_detectada": (
                        request.POST.get("person_name") or "Desconocido/a"
                    )[:100],
                    "epp_correcto": _post_bool(request.POST.get("epp_correcto")),
                    "descripcion": f"{event.get_event_type_display()}: {event.details}",
                    "evidencia": image_path,
                },
            )
    except IntegrityError:
        event = SecurityEvent.objects.get(source_event_key=source_key)
        return JsonResponse(
            {"success": True, "duplicate": True, "event_id": event.pk},
            status=200,
        )

    return JsonResponse(
        {"success": True, "duplicate": False, "event_id": event.pk},
        status=201,
    )
