# Generated manually on 2026-06-03

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("camera", "0007_alter_authorizedperson_options_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="securityevent",
            name="event_type",
            field=models.CharField(
                choices=[
                    ("face_recognized", "Rostro reconocido"),
                    ("face_unknown", "Rostro desconocido"),
                    ("authorized_object", "Objeto autorizado"),
                    ("dangerous_object", "Objeto peligroso detectado"),
                    ("unauthorized_access", "Acceso no autorizado"),
                ],
                max_length=50,
            ),
        ),
    ]
