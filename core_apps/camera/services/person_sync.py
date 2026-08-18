"""Sincroniza el registro web de personal desde cloud hacia el nodo edge."""

from __future__ import annotations

import json
import logging
import threading
from io import BytesIO

import requests
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import close_old_connections
from django.http import FileResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_GET
from PIL import Image, ImageOps, UnidentifiedImageError

from core_apps.camera.models import AuthorizedPerson
from core_apps.camera.services.event_sync import is_sync_request_authorized
from core_apps.camera.utils import save_authorized_face_image


logger = logging.getLogger(__name__)
_WORKER_LOCK = threading.Lock()
_WORKER_STARTED = False


def _cloud_only_response():
    return JsonResponse({"success": False, "error": "cloud_only"}, status=404)


@require_GET
def authorized_people_manifest(request):
    """Lista privada que el edge consulta periódicamente por HTTPS."""

    if getattr(settings, "SMRI_NODE_ROLE", "edge") != "cloud":
        return _cloud_only_response()
    if not settings.SMRI_EVENT_SYNC_TOKEN:
        return JsonResponse({"success": False, "error": "sync_not_configured"}, status=503)
    if not is_sync_request_authorized(request):
        return JsonResponse({"success": False, "error": "unauthorized"}, status=401)

    people = []
    for person in AuthorizedPerson.objects.order_by("pk"):
        image_url = ""
        if person.face_image_path:
            image_url = request.build_absolute_uri(
                reverse("authorized_person_image", args=(person.pk,))
            )
        people.append(
            {
                "id": person.pk,
                "nombres": person.nombres or "",
                "apellidos": person.apellidos or "",
                "celular": person.celular or "",
                "correo": person.correo or "",
                "cargo": person.cargo or "",
                "is_active": person.is_active,
                "updated_at": person.updated_at.isoformat(),
                "image_url": image_url,
            }
        )
    return JsonResponse({"success": True, "people": people})


@require_GET
def authorized_person_image(request, person_id):
    if getattr(settings, "SMRI_NODE_ROLE", "edge") != "cloud":
        return _cloud_only_response()
    if not is_sync_request_authorized(request):
        return JsonResponse({"success": False, "error": "unauthorized"}, status=401)

    person = get_object_or_404(AuthorizedPerson, pk=person_id)
    if not person.face_image_path or not default_storage.exists(person.face_image_path):
        return JsonResponse({"success": False, "error": "image_not_found"}, status=404)
    return FileResponse(
        default_storage.open(person.face_image_path, "rb"),
        content_type="image/jpeg",
    )


def _bearer_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {settings.SMRI_EVENT_SYNC_TOKEN}"}


def _remote_timestamp(value: str):
    parsed = parse_datetime(value or "")
    if parsed is not None and timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def _needs_update(local_person, remote_updated_at, remote_active: bool) -> bool:
    if local_person is None:
        return True
    if local_person.is_active != remote_active:
        return True
    if not local_person.face_encoding and remote_active:
        return True
    if remote_updated_at is None or local_person.source_updated_at is None:
        return True
    return local_person.source_updated_at < remote_updated_at


def _face_encoding_from_image(image_bytes: bytes) -> tuple[str, bytes]:
    try:
        import face_recognition  # type: ignore
    except Exception as exc:
        raise RuntimeError("face_recognition no está disponible en el nodo edge") from exc

    try:
        with Image.open(BytesIO(image_bytes)) as source:
            normalized = ImageOps.exif_transpose(source).convert("RGB")
            normalized.thumbnail((800, 800), Image.Resampling.LANCZOS)
            buffer = BytesIO()
            normalized.save(buffer, format="JPEG", quality=90, optimize=True)
            normalized_bytes = buffer.getvalue()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise RuntimeError("La fotografía sincronizada no es una imagen válida") from exc

    image_data = face_recognition.load_image_file(BytesIO(normalized_bytes))
    locations = face_recognition.face_locations(image_data, model="hog")
    if len(locations) != 1:
        raise RuntimeError(
            "La fotografía debe contener exactamente un rostro para generar el encoding"
        )
    encodings = face_recognition.face_encodings(image_data, locations, num_jitters=1)
    if not encodings:
        raise RuntimeError("No se pudo generar la codificación facial")
    return json.dumps(encodings[0].tolist()), normalized_bytes


def _sync_person(remote: dict) -> bool:
    email = str(remote.get("correo") or "").strip().lower()
    if not email:
        return False
    remote_updated_at = _remote_timestamp(str(remote.get("updated_at") or ""))
    remote_active = bool(remote.get("is_active", True))
    local_person = AuthorizedPerson.objects.filter(correo__iexact=email).first()
    if not _needs_update(local_person, remote_updated_at, remote_active):
        return False

    common_defaults = {
        "nombres": str(remote.get("nombres") or "")[:100],
        "apellidos": str(remote.get("apellidos") or "")[:100],
        "celular": str(remote.get("celular") or "")[:20],
        "cargo": str(remote.get("cargo") or "")[:100],
        "is_active": remote_active,
        "source_updated_at": remote_updated_at,
    }
    if not remote_active:
        if local_person is not None:
            for field, value in common_defaults.items():
                setattr(local_person, field, value)
            local_person.save(update_fields=(*common_defaults.keys(), "updated_at"))
            return True
        return False

    image_url = str(remote.get("image_url") or "")
    if not image_url:
        raise RuntimeError(f"La persona {email} no tiene fotografía en cloud")
    response = requests.get(
        image_url,
        headers=_bearer_headers(),
        timeout=settings.SMRI_EVENT_SYNC_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    encoding_json, normalized_bytes = _face_encoding_from_image(response.content)
    image_path = save_authorized_face_image(
        ContentFile(normalized_bytes, name="authorized_face.jpg"),
        email,
    )
    if not image_path:
        raise RuntimeError(f"No se pudo guardar localmente la fotografía de {email}")

    common_defaults.update(
        {
            "face_encoding": encoding_json,
            "face_image_path": image_path,
        }
    )
    AuthorizedPerson.objects.update_or_create(correo=email, defaults=common_defaults)
    return True


def sync_authorized_people() -> dict[str, int]:
    if not settings.SMRI_PERSON_SYNC_URL or not settings.SMRI_EVENT_SYNC_TOKEN:
        return {"updated": 0, "failed": 0}
    response = requests.get(
        settings.SMRI_PERSON_SYNC_URL,
        headers=_bearer_headers(),
        timeout=settings.SMRI_EVENT_SYNC_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json()
    result = {"updated": 0, "failed": 0}
    for remote in payload.get("people", []):
        try:
            if _sync_person(remote):
                result["updated"] += 1
        except Exception as exc:
            result["failed"] += 1
            logger.warning(
                "No se pudo sincronizar la persona %s: %s",
                remote.get("correo") or remote.get("id"),
                exc,
            )
    return result


def _worker_loop() -> None:
    while True:
        try:
            close_old_connections()
            sync_authorized_people()
        except Exception:
            logger.exception("Fallo inesperado sincronizando personal autorizado")
        finally:
            close_old_connections()
        threading.Event().wait(settings.SMRI_PERSON_SYNC_INTERVAL_SECONDS)


def start_person_sync_worker() -> bool:
    global _WORKER_STARTED
    if not getattr(settings, "SMRI_EDGE_ENABLED", False):
        return False
    if not settings.SMRI_PERSON_SYNC_URL or not settings.SMRI_EVENT_SYNC_TOKEN:
        return False
    with _WORKER_LOCK:
        if _WORKER_STARTED:
            return True
        threading.Thread(
            target=_worker_loop,
            name="smri-person-sync",
            daemon=True,
        ).start()
        _WORKER_STARTED = True
    logger.info("Sincronizador de personal cloud->edge iniciado")
    return True
