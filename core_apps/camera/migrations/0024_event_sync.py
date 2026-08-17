from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ("camera", "0023_securityevent_category"),
    ]

    operations = [
        migrations.AddField(
            model_name="securityevent",
            name="source_event_key",
            field=models.CharField(
                blank=True,
                help_text="Identificador idempotente asignado por el nodo edge de origen.",
                max_length=180,
                null=True,
                unique=True,
            ),
        ),
        migrations.CreateModel(
            name="EventSyncOutbox",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "source_key",
                    models.CharField(max_length=180, unique=True),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("PENDING", "Pendiente"),
                            ("RETRY", "Reintentar"),
                            ("SYNCED", "Sincronizado"),
                        ],
                        default="PENDING",
                        max_length=10,
                    ),
                ),
                ("attempts", models.PositiveIntegerField(default=0)),
                (
                    "next_attempt_at",
                    models.DateTimeField(
                        db_index=True,
                        default=django.utils.timezone.now,
                    ),
                ),
                ("last_error", models.TextField(blank=True, default="")),
                ("synced_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "event",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="sync_outbox",
                        to="camera.securityevent",
                    ),
                ),
            ],
            options={
                "verbose_name": "Evento pendiente de sincronización",
                "verbose_name_plural": "Eventos pendientes de sincronización",
                "ordering": ("next_attempt_at", "id"),
            },
        ),
    ]
