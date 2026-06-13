from django.db import migrations, models


def mark_existing_events(apps, schema_editor):
    SecurityEvent = apps.get_model("camera", "SecurityEvent")
    SecurityEvent.objects.update(
        email_status="SKIPPED",
        email_error="Incidente creado antes de habilitar el seguimiento de correos.",
    )


class Migration(migrations.Migration):

    dependencies = [
        ("camera", "0011_camera_restricted_zone"),
    ]

    operations = [
        migrations.AddField(
            model_name="securityevent",
            name="email_status",
            field=models.CharField(
                choices=[
                    ("PENDING", "Pendiente"),
                    ("SENT", "Enviado"),
                    ("FAILED", "Fallido"),
                    ("SKIPPED", "No aplica"),
                ],
                default="PENDING",
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name="securityevent",
            name="email_recipient",
            field=models.EmailField(blank=True, default="", max_length=254),
        ),
        migrations.AddField(
            model_name="securityevent",
            name="email_cc",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="securityevent",
            name="email_sent_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="securityevent",
            name="email_error",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.RunPython(mark_existing_events, migrations.RunPython.noop),
    ]
