# Generated manually for settings preferences.

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="UserSetting",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "theme_color",
                    models.CharField(
                        choices=[
                            ("verde", "Verde"),
                            ("azul", "Azul"),
                            ("morado", "Morado"),
                            ("rojo", "Rojo"),
                        ],
                        default="azul",
                        max_length=20,
                    ),
                ),
                ("compact_layout", models.BooleanField(default=False)),
                ("dark_mode", models.BooleanField(default=False)),
                ("security_alerts", models.BooleanField(default=True)),
                ("email_alerts", models.BooleanField(default=False)),
                ("extra_verification", models.BooleanField(default=False)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="settings_profile",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
    ]
