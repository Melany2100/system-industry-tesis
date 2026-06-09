import re

from django.db import migrations, models


DEFAULT_EVENT_LEVELS = {
    "face_recognized": "BAJO",
    "face_unknown": "MEDIO",
    "ppe_missing": "ALTO",
    "intrusion": "ALTO",
    "authorized_object": "BAJO",
    "unauthorized_object": "MEDIO",
    "dangerous_object": "ALTO",
    "unauthorized_access": "ALTO",
}
LEVEL_PATTERN = re.compile(
    r"(?:nivel|prioridad)\s*:?\s*(BAJO|BAJA|MEDIO|MEDIA|ALTO|ALTA|CRITICO|CRÍTICO)",
    re.IGNORECASE,
)


def normalize_level(value, default="MEDIO"):
    if not value:
        return default

    level = str(value).strip().upper().replace("Í", "I")
    aliases = {
        "BAJA": "BAJO",
        "MEDIA": "MEDIO",
        "ALTA": "ALTO",
    }
    level = aliases.get(level, level)

    return level if level in {"BAJO", "MEDIO", "ALTO", "CRITICO"} else default


def backfill_event_severity(apps, schema_editor):
    SecurityEvent = apps.get_model("camera", "SecurityEvent")

    for event in SecurityEvent.objects.all().iterator():
        match = LEVEL_PATTERN.search(event.details or "")
        severity = normalize_level(
            match.group(1) if match else None,
            default=DEFAULT_EVENT_LEVELS.get(event.event_type, "MEDIO"),
        )
        event.severity = severity
        event.save(update_fields=["severity"])


class Migration(migrations.Migration):

    dependencies = [
        ("camera", "0009_securityevent_review_management"),
    ]

    operations = [
        migrations.AddField(
            model_name="securityevent",
            name="severity",
            field=models.CharField(
                choices=[
                    ("BAJO", "Bajo"),
                    ("MEDIO", "Medio"),
                    ("ALTO", "Alto"),
                    ("CRITICO", "Crítico"),
                ],
                default="MEDIO",
                max_length=10,
            ),
        ),
        migrations.AlterField(
            model_name="securityevent",
            name="event_type",
            field=models.CharField(
                choices=[
                    ("face_recognized", "Rostro reconocido"),
                    ("face_unknown", "Rostro desconocido"),
                    ("ppe_missing", "Falta de EPP"),
                    ("intrusion", "Intrusión"),
                    ("authorized_object", "Objeto autorizado"),
                    ("unauthorized_object", "Objeto no autorizado"),
                    ("dangerous_object", "Objeto peligroso detectado"),
                    ("unauthorized_access", "Acceso no autorizado"),
                ],
                max_length=50,
            ),
        ),
        migrations.RunPython(backfill_event_severity, migrations.RunPython.noop),
    ]
