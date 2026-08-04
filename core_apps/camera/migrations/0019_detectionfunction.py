from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('camera', '0018_remove_camera_api_token_remove_camera_push_token'),
    ]

    operations = [
        migrations.CreateModel(
            name='DetectionFunction',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(help_text='Nombre administrativo para identificar esta función.', max_length=120, verbose_name='Nombre de la función')),
                ('event_type', models.CharField(choices=[('face_recognized', 'Rostro reconocido'), ('face_unknown', 'Rostro desconocido'), ('intrusion', 'Intrusión'), ('unauthorized_access', 'Acceso no autorizado'), ('phone_usage', 'Distracción por uso de celular'), ('ppe_missing', 'Falta de EPP'), ('fall_detected', 'Movimiento o caída'), ('authorized_object', 'Objeto autorizado'), ('unauthorized_object', 'Objeto no autorizado'), ('dangerous_object', 'Objeto peligroso detectado'), ('collision_risk', 'Riesgo de choque'), ('cut_risk', 'Riesgo de corte')], help_text='Selecciona una regla que ya se encuentra implementada en el código.', max_length=50, unique=True, verbose_name='Regla de detección')),
                ('severity', models.CharField(choices=[('BAJO', 'Bajo'), ('MEDIO', 'Medio'), ('ALTO', 'Alto'), ('CRITICO', 'Crítico')], default='MEDIO', max_length=10, verbose_name='Nivel de riesgo')),
                ('is_active', models.BooleanField(default=True, verbose_name='Activa')),
                ('description', models.TextField(blank=True, help_text='Explica el objetivo operativo de la función.', verbose_name='Descripción')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'Función de detección',
                'verbose_name_plural': 'Funciones de detección',
                'ordering': ('name',),
            },
        ),
    ]
