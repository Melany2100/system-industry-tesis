from django.core.management.base import BaseCommand
from django.contrib.auth.models import Group, User

from core_apps.camera.models import SecurityEvent
from core_apps.informes.models import Informe


class Command(BaseCommand):
    help = "Crea datos de demostración (usuario + eventos + informes)"

    def add_arguments(self, parser):
        parser.add_argument('--username', default='admin', help='Usuario a crear/usar')
        parser.add_argument('--password', default='admin12345', help='Contraseña')
        parser.add_argument('--events', type=int, default=8, help='Cantidad de eventos/informes a crear')

    def handle(self, *args, **options):
        username = options['username']
        password = options['password']
        n_events = options['events']

        user, created = User.objects.get_or_create(username=username)
        admin_group, _ = Group.objects.get_or_create(name="Administrador")

        if created:
            user.set_password(password)
            user.is_staff = True
            user.is_superuser = True
            user.save()
            self.stdout.write(self.style.SUCCESS(f"Usuario creado: {username} / {password}"))
        else:
            self.stdout.write(self.style.WARNING(f"Usuario ya existe: {username}"))

        user.groups.add(admin_group)

        # Crear eventos + informes
        tipos = ['face_recognized', 'face_unknown', 'dangerous_object', 'unauthorized_access']
        for i in range(n_events):
            event_type = tipos[i % len(tipos)]
            details = f"Evento demo #{i+1} ({event_type})"
            event = SecurityEvent.objects.create(
                event_type=event_type,
                details=details,
                related_user=user if event_type == 'face_recognized' else None,
                resolved=False,
            )
            Informe.objects.create(
                camara='Cámara 1',
                persona_detectada=user.username if event.related_user else 'Desconocido',
                epp_correcto=(event_type == 'face_recognized'),
                descripcion=f"{event.get_event_type_display()}: {details}",
            )

        self.stdout.write(self.style.SUCCESS(f"Listo: creados {n_events} eventos e informes."))
