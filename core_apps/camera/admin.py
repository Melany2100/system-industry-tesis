from django.contrib import admin
from .models import AuthorizedPerson, SecurityEvent, Camera


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
