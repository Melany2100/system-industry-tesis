from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("camera", "0012_securityevent_email_delivery"),
    ]

    operations = [
        migrations.AlterField(
            model_name="securityevent",
            name="event_type",
            field=models.CharField(
                choices=[
                    ("face_recognized", "Rostro reconocido"),
                    ("face_unknown", "Rostro desconocido"),
                    ("ppe_missing", "Falta de EPP"),
                    ("intrusion", "Intrusi\u00f3n"),
                    ("authorized_object", "Objeto autorizado"),
                    ("unauthorized_object", "Objeto no autorizado"),
                    ("dangerous_object", "Objeto peligroso detectado"),
                    ("fall_detected", "Posible caida"),
                    ("phone_usage", "Uso prolongado de celular"),
                    ("collision_risk", "Riesgo de choque"),
                    ("cut_risk", "Riesgo de corte"),
                    ("unauthorized_access", "Acceso no autorizado"),
                ],
                max_length=50,
            ),
        ),
    ]
