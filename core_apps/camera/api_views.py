from pathlib import Path
import uuid

from django.core.files.storage import default_storage
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .models import Camera, SecurityEvent


@csrf_exempt
@require_POST
def receive_detection_event(request):
    token = request.headers.get("X-Agent-Token")

    if not token:
        return JsonResponse(
            {"ok": False, "error": "Token requerido"},
            status=401
        )

    try:
        camera = Camera.objects.get(api_token=token, is_active=True)
    except Camera.DoesNotExist:
        return JsonResponse(
            {"ok": False, "error": "Cámara no autorizada"},
            status=403
        )

    event_type = request.POST.get("event_type")
    severity = request.POST.get("severity", "MEDIO")
    object_label = request.POST.get("object_label", "")
    details = request.POST.get("details", "")
    confidence = request.POST.get("confidence", "")

    if not event_type:
        return JsonResponse(
            {"ok": False, "error": "event_type es requerido"},
            status=400
        )

    if confidence:
        details = f"{details} | Confianza: {confidence}"

    image_path = ""

    image = request.FILES.get("image")
    if image:
        now = timezone.now()
        extension = Path(image.name).suffix or ".jpg"
        filename = f"events/{now:%Y/%m/%d}/{uuid.uuid4().hex}{extension}"
        image_path = default_storage.save(filename, image)

    event = SecurityEvent.objects.create(
        event_type=event_type,
        severity=severity,
        object_label=object_label,
        details=details,
        image_path=image_path,
        timestamp=timezone.now(),
        should_alert=True,
    )

    camera.last_seen = timezone.now()
    camera.save(update_fields=["last_seen"])

    return JsonResponse({
        "ok": True,
        "event_id": event.id
    })
