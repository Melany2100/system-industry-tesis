from django.core.management.base import BaseCommand, CommandError

from core_apps.camera.services.event_sync import (
    backfill_event_outbox,
    process_due_events,
)


class Command(BaseCommand):
    help = "Encola eventos locales y ejecuta un lote de sincronización con cloud."

    def add_arguments(self, parser):
        parser.add_argument(
            "--backfill",
            type=int,
            default=0,
            metavar="N",
            help="Encola hasta N eventos locales recientes que no tengan salida.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=100,
            help="Máximo de elementos que se intentará enviar en este lote.",
        )

    def handle(self, *args, **options):
        backfill = max(0, options["backfill"])
        limit = max(1, options["limit"])
        if backfill:
            created = backfill_event_outbox(backfill)
            self.stdout.write(f"Eventos incorporados a la cola: {created}")

        result = process_due_events(limit=limit)
        self.stdout.write(
            self.style.SUCCESS(
                f"Sincronizados: {result['synced']}; fallidos/reprogramados: {result['failed']}"
            )
        )
        if result["failed"]:
            raise CommandError(
                "Algunos envíos fallaron. Consulta EventSyncOutbox.last_error; "
                "la cola automática volverá a intentarlos."
            )
