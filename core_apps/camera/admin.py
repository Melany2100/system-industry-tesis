from django.contrib import admin
from .models import AuthorizedPerson, SecurityEvent, Camera, DetectionRule


@admin.register(AuthorizedPerson)
class AuthorizedPersonAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "nombres",
        "apellidos",
        "correo",
        "celular",
        "cargo",
        "is_active",
        "created_at",
    )
    list_filter = (
        "is_active",
        "cargo",
        "created_at",
    )
    search_fields = (
        "nombres",
        "apellidos",
        "correo",
        "celular",
        "cargo",
    )
    readonly_fields = (
        "created_at",
        "updated_at",
    )


@admin.register(Camera)
class CameraAdmin(admin.ModelAdmin):
    list_display = ("id", "nombre", "source", "ubicacion", "is_active")
    list_filter = ("is_active",)
    search_fields = ("nombre", "source", "ubicacion")


@admin.register(DetectionRule)
class DetectionRuleAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "name",
        "model_label",
        "category",
        "risk_level",
        "event_type",
        "should_alert",
        "requires_duration",
        "max_allowed_seconds",
        "min_confidence",
        "cooldown_seconds",
        "is_active",
    )
    list_filter = (
        "category",
        "risk_level",
        "event_type",
        "should_alert",
        "requires_duration",
        "is_active",
    )
    search_fields = (
        "name",
        "model_label",
        "aliases",
        "description",
    )
    readonly_fields = (
        "created_at",
        "updated_at",
    )


@admin.register(SecurityEvent)
class SecurityEventAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "event_type",
        "camera",
        "object_label",
        "risk_level",
        "should_alert",
        "confidence",
        "duration_seconds",
        "authorized_person",
        "timestamp",
        "resolved",
        "related_user",
        "reviewed_by",
        "managed_by",
    )
    list_filter = (
        "event_type",
        "category",
        "risk_level",
        "should_alert",
        "resolved",
        "camera",
        "timestamp",
        "reviewed_at",
        "managed_at",
    )
    search_fields = (
        "details",
        "object_label",
        "authorized_person__nombres",
        "authorized_person__apellidos",
        "authorized_person__correo",
        "reviewed_by__username",
        "managed_by__username",
    )
