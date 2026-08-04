from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('camera', '0019_detectionfunction'),
    ]

    operations = [
        migrations.AlterField(
            model_name='detectionfunction',
            name='name',
            field=models.CharField(
                help_text=(
                    'Escribe una categoría propia, aunque todavía no exista en el sistema. '
                    'Ejemplo: Prohibición de celulares en producción.'
                ),
                max_length=120,
                verbose_name='Nombre de la nueva categoría',
            ),
        ),
        migrations.AlterField(
            model_name='detectionfunction',
            name='event_type',
            field=models.CharField(
                choices=[
                    ('face_recognized', 'Rostro reconocido'),
                    ('face_unknown', 'Rostro desconocido'),
                    ('intrusion', 'Intrusión'),
                    ('unauthorized_access', 'Acceso no autorizado'),
                    ('phone_usage', 'Distracción por uso de celular'),
                    ('ppe_missing', 'Falta de EPP'),
                    ('fall_detected', 'Movimiento o caída'),
                    ('authorized_object', 'Objeto autorizado'),
                    ('unauthorized_object', 'Objeto no autorizado'),
                    ('dangerous_object', 'Objeto peligroso detectado'),
                    ('collision_risk', 'Riesgo de choque'),
                    ('cut_risk', 'Riesgo de corte'),
                ],
                help_text='Selecciona la regla programada que ejecutará esta nueva categoría.',
                max_length=50,
                unique=True,
                verbose_name='Regla de detección existente',
            ),
        ),
    ]
