from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("camera", "0010_securityevent_severity_event_types"),
    ]

    operations = [
        migrations.AddField(
            model_name="camera",
            name="restricted_zone_enabled",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="camera",
            name="restricted_zone_name",
            field=models.CharField(blank=True, default="Zona restringida", max_length=120),
        ),
        migrations.AddField(
            model_name="camera",
            name="restricted_zone_x",
            field=models.FloatField(default=0.0),
        ),
        migrations.AddField(
            model_name="camera",
            name="restricted_zone_y",
            field=models.FloatField(default=0.0),
        ),
        migrations.AddField(
            model_name="camera",
            name="restricted_zone_width",
            field=models.FloatField(default=0.0),
        ),
        migrations.AddField(
            model_name="camera",
            name="restricted_zone_height",
            field=models.FloatField(default=0.0),
        ),
    ]
