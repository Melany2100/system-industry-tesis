from django.contrib import admin
from .models import AuthorizedPerson, SecurityEvent, Camera


@admin.register(AuthorizedPerson)
class AuthorizedPersonAdmin(admin.ModelAdmin):
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


@admin.register(Camera)
class CameraAdmin(admin.ModelAdmin):
    list_display = ('id', 'nombre', 'source', 'ubicacion', 'is_active')
    list_filter = ('is_active',)
    search_fields = ('nombre', 'source', 'ubicacion')


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
        'related_user',
        'reviewed_by',
        'managed_by',
    )

    list_filter = (
        'event_type',
        'severity',
        'resolved',
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
    )
