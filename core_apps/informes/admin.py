from datetime import timedelta

from django.contrib import admin
from django.core.files.storage import default_storage
from django.utils import timezone

from .models import Informe


@admin.register(Informe)
class InformeAdmin(admin.ModelAdmin):
    actions = (
        "delete_reports_older_than_30_days",
        "delete_reports_older_than_60_days",
        "delete_reports_older_than_90_days",
    )
    date_hierarchy = "fecha"
    list_display = (
        "id",
        "fecha",
        "camara",
        "persona_detectada",
        "epp_correcto",
        "security_event",
    )
    list_filter = ("epp_correcto", "camara", "fecha")
    search_fields = (
        "camara",
        "persona_detectada",
        "descripcion",
        "security_event__details",
    )
    readonly_fields = ("fecha",)
    list_per_page = 50

    def delete_queryset(self, request, queryset):
        self._delete_reports_with_evidence(queryset)

    def delete_model(self, request, obj):
        self._delete_reports_with_evidence(Informe.objects.filter(pk=obj.pk))

    def _delete_reports_with_evidence(self, queryset):
        evidence_paths = [
            report.evidencia.name
            for report in queryset.only("id", "evidencia")
            if report.evidencia
        ]
        queryset.delete()

        for path in evidence_paths:
            try:
                if default_storage.exists(path):
                    default_storage.delete(path)
            except Exception:
                pass

    def _delete_older_than(self, request, days):
        cutoff = timezone.now() - timedelta(days=days)
        queryset = Informe.objects.filter(fecha__lte=cutoff)
        total = queryset.count()
        self._delete_reports_with_evidence(queryset)
        self.message_user(
            request,
            f"{total} informe(s) anterior(es) a {days} dias fueron eliminados.",
        )

    @admin.action(description="Eliminar informes con mas de 30 dias")
    def delete_reports_older_than_30_days(self, request, queryset):
        self._delete_older_than(request, 30)

    @admin.action(description="Eliminar informes con mas de 60 dias")
    def delete_reports_older_than_60_days(self, request, queryset):
        self._delete_older_than(request, 60)

    @admin.action(description="Eliminar informes con mas de 90 dias")
    def delete_reports_older_than_90_days(self, request, queryset):
        self._delete_older_than(request, 90)
