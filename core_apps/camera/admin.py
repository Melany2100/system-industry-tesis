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
        'camera',
        'authorized_person',
        'timestamp',
        'resolved',
        'related_user',
    )

    list_filter = (
        'event_type',
        'resolved',
        'camera',
        'timestamp',
    )

    search_fields = (
        'details',
        'authorized_person__nombres',
        'authorized_person__apellidos',
        'authorized_person__correo',
    )