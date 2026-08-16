from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("camera", "0020_detectionfunction_category_labels"),
    ]

    operations = [
        migrations.AddField(
            model_name="camera",
            name="stream_path",
            field=models.SlugField(
                blank=True,
                default="",
                help_text=(
                    "Ruta publicada en MediaMTX, sin dominio ni barras. "
                    "Si queda vacía se usará camera-ID."
                ),
                max_length=120,
            ),
        ),
    ]
