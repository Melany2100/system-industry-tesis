from django import forms
from django.contrib import admin
from .models import AuthorizedPerson, Camera, DetectionFunction, SecurityEvent


DETECTION_RULE_CHOICES = (
    (
        "Personas y accesos",
        (
            ("face_recognized", "Rostro reconocido"),
            ("face_unknown", "Rostro desconocido"),
            ("intrusion", "Intrusión"),
            ("unauthorized_access", "Acceso no autorizado"),
        ),
    ),
    (
        "Conducta y seguridad personal",
        (
            ("phone_usage", "Distracción por uso de celular"),
            ("ppe_missing", "Falta de EPP"),
            ("fall_detected", "Movimiento o caída"),
        ),
    ),
    (
        "Objetos y riesgos industriales",
        (
            ("authorized_object", "Objeto autorizado"),
            ("unauthorized_object", "Objeto no autorizado"),
            ("dangerous_object", "Objeto peligroso detectado"),
            ("collision_risk", "Riesgo de choque"),
            ("cut_risk", "Riesgo de corte"),
        ),
    ),
)


class DetectionFunctionAdminForm(forms.ModelForm):
    event_type = forms.ChoiceField(
        label="Regla de detección existente",
        choices=DETECTION_RULE_CHOICES,
        help_text=(
            "La regla ya está programada en el sistema. La categoría nueva tendrá "
            "el nombre libre indicado en el campo anterior."
        ),
    )

    class Meta:
        model = DetectionFunction
        fields = "__all__"
        widgets = {
            "description": forms.Textarea(attrs={
                "rows": 4,
                "placeholder": "Describe el objetivo de esta función de detección.",
            }),
        }


@admin.register(DetectionFunction)
class DetectionFunctionAdmin(admin.ModelAdmin):
    form = DetectionFunctionAdminForm
    list_display = ('name', 'event_type', 'severity', 'is_active', 'updated_at')
    list_filter = ('is_active', 'event_type', 'severity')
    search_fields = ('name', 'description')
    readonly_fields = ('created_at', 'updated_at')
    fieldsets = (
        (
            'Nueva categoría de detección',
            {
                'fields': ('name', 'event_type', 'severity', 'is_active'),
                'description': (
                    'Primero escribe el nombre de una categoría nueva y luego '
                    'vincúlala con una regla que ya está programada. Esto no crea '
                    'un evento histórico ni requiere escribir código nuevo.'
                ),
            },
        ),
        ('Descripción operativa', {'fields': ('description',)}),
        ('Auditoría', {'classes': ('collapse',), 'fields': ('created_at', 'updated_at')}),
    )


@admin.register(AuthorizedPerson)
class AuthorizedPersonAdmin(admin.ModelAdmin):
    actions = ("activate_people", "deactivate_people")
    list_display = (
        'id',
        'nombres',
        'apellidos',
        'correo',
        'celular',
        'cargo',
        'is_active',
        'created_at',
    )

    list_filter = (
        'is_active',
        'cargo',
        'created_at',
    )

    search_fields = (
        'nombres',
        'apellidos',
        'correo',
        'celular',
        'cargo',
    )

    readonly_fields = (
        'created_at',
        'updated_at',
    )

    @admin.action(description="Activar personas seleccionadas")
    def activate_people(self, request, queryset):
        updated = queryset.update(is_active=True)
        self.message_user(request, f"{updated} persona(s) activada(s).")

    @admin.action(description="Desactivar personas seleccionadas")
    def deactivate_people(self, request, queryset):
        updated = queryset.update(is_active=False)
        self.message_user(request, f"{updated} persona(s) desactivada(s).")


@admin.register(Camera)
class CameraAdmin(admin.ModelAdmin):
    actions = ("activate_cameras", "deactivate_cameras")
    list_display = ('id', 'nombre', 'source', 'ubicacion', 'is_active', 'last_seen')
    list_filter = ('is_active',)
    search_fields = ('nombre', 'source', 'ubicacion')
    readonly_fields = ('last_seen',)

    @admin.action(description="Activar camaras seleccionadas")
    def activate_cameras(self, request, queryset):
        updated = queryset.update(is_active=True)
        self.message_user(request, f"{updated} camara(s) activada(s).")

    @admin.action(description="Desactivar camaras seleccionadas")
    def deactivate_cameras(self, request, queryset):
        updated = queryset.update(is_active=False)
        self.message_user(request, f"{updated} camara(s) desactivada(s).")


@admin.register(SecurityEvent)
class SecurityEventAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'event_type',
        'severity',
        'camera',
        'authorized_person',
        'timestamp',
        'resolved',
        'email_status',
        'email_sent_at',
        'related_user',
        'reviewed_by',
        'managed_by',
    )

    list_filter = (
        'event_type',
        'severity',
        'resolved',
        'email_status',
        'camera',
        'timestamp',
        'reviewed_at',
        'managed_at',
    )

    search_fields = (
        'details',
        'authorized_person__nombres',
        'authorized_person__apellidos',
        'authorized_person__correo',
        'reviewed_by__username',
        'managed_by__username',
        'email_recipient',
        'email_cc',
    )

    readonly_fields = (
        'email_status',
        'email_recipient',
        'email_cc',
        'email_sent_at',
        'email_error',
    )

    def has_add_permission(self, request):
        """El historial solo contiene evidencia producida por los detectores."""
        return False
