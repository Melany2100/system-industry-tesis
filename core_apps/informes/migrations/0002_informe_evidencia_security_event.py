# Generated manually on 2026-06-06

from django.db import migrations, models
import django.db.models.deletion


def backfill_informe_evidencia(apps, schema_editor):
    Informe = apps.get_model("informes", "Informe")
    SecurityEvent = apps.get_model("camera", "SecurityEvent")
    event_type_field = SecurityEvent._meta.get_field("event_type")
    event_labels = dict(event_type_field.choices)

    events = (
        SecurityEvent.objects
        .exclude(image_path__isnull=True)
        .exclude(image_path="")
        .order_by("-timestamp")
    )

    for event in events:
        label = event_labels.get(event.event_type, event.event_type)
        expected_description = f"{label}: {event.details}"
        informe = (
            Informe.objects
            .filter(security_event__isnull=True, descripcion=expected_description)
            .order_by("-fecha")
            .first()
        )

        if informe is None:
            informe = (
                Informe.objects
                .filter(security_event__isnull=True, descripcion__endswith=event.details)
                .order_by("-fecha")
                .first()
            )

        if informe is None:
            continue

        informe.security_event_id = event.id
        informe.evidencia = event.image_path
        informe.save(update_fields=["security_event", "evidencia"])


class Migration(migrations.Migration):

    dependencies = [
        ("camera", "0009_securityevent_review_management"),
        ("informes", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="informe",
            name="evidencia",
            field=models.FileField(blank=True, null=True, upload_to="informes/evidencias/"),
        ),
        migrations.AddField(
            model_name="informe",
            name="security_event",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="informes",
                to="camera.securityevent",
            ),
        ),
        migrations.RunPython(backfill_informe_evidencia, migrations.RunPython.noop),
    ]
