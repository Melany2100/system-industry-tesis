from django.core.management.base import BaseCommand, CommandError

from core_apps.camera.services.person_sync import sync_authorized_people


class Command(BaseCommand):
    help = "Descarga desde cloud el personal autorizado y genera los encodings locales."

    def handle(self, *args, **options):
        result = sync_authorized_people()
        self.stdout.write(
            self.style.SUCCESS(
                f"Personal actualizado: {result['updated']}; fallidos: {result['failed']}"
            )
        )
        if result["failed"]:
            raise CommandError(
                "Algunos registros no pudieron procesarse. Revisa que cada foto "
                "sea frontal y contenga exactamente un rostro."
            )
