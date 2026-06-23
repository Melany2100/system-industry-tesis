from datetime import timedelta

from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand
from django.utils import timezone

from core_apps.informes.models import Informe


class Command(BaseCommand):
    help = "Elimina informes registrados hace 90 dias o mas."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=90,
            help="Antiguedad minima en dias para depurar informes.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Muestra cuantos informes se eliminarian sin borrar datos.",
        )

    def handle(self, *args, **options):
        days = max(1, int(options["days"]))
        cutoff = timezone.now() - timedelta(days=days)
        reports = Informe.objects.filter(fecha__lte=cutoff)
        total = reports.count()

        if options["dry_run"]:
            self.stdout.write(
                self.style.WARNING(
                    f"Se eliminarian {total} informes registrados hasta {cutoff:%Y-%m-%d %H:%M:%S}."
                )
            )
            return

        evidence_paths = [
            report.evidencia.name
            for report in reports.only("id", "evidencia")
            if report.evidencia
        ]
        deleted_count, _ = reports.delete()

        for path in evidence_paths:
            try:
                if default_storage.exists(path):
                    default_storage.delete(path)
            except Exception as exc:
                self.stderr.write(f"No se pudo eliminar evidencia {path}: {exc}")

        self.stdout.write(
            self.style.SUCCESS(
                f"Depuracion completada: {deleted_count} registros eliminados."
            )
        )
