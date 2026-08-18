from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("camera", "0024_event_sync"),
    ]

    operations = [
        migrations.AddField(
            model_name="authorizedperson",
            name="source_updated_at",
            field=models.DateTimeField(
                blank=True,
                help_text="Versión del registro recibido desde el nodo cloud.",
                null=True,
            ),
        ),
    ]
