from django.conf import settings
from django.contrib.auth.models import User
from django.core.files.storage import default_storage
from django.db import models

class AuthorizedPerson(models.Model):
    #user = models.OneToOneField(User, on_delete=models.CASCADE)
    nombres = models.CharField(max_length=100, blank=True, null=True)
    apellidos = models.CharField(max_length=100, blank=True, null=True)
    celular = models.CharField(max_length=20, blank=True, null=True)
    correo = models.EmailField(unique=True, blank=True, null=True)
    cargo = models.CharField(max_length=100, blank=True, null=True)

    face_encoding = models.TextField()
    face_image_path = models.CharField(max_length=500, blank=True, null=True)

    is_active = models.BooleanField(default=True)

    registered_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="authorized_people_registered"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["nombres", "apellidos"]
        verbose_name = "Persona autorizada"
        verbose_name_plural = "Personas autorizadas"

    def __str__(self):
        return f"{self.nombres} {self.apellidos} - {self.cargo or 'Sin cargo'}"

    def get_full_name(self):
        return f"{self.nombres} {self.apellidos}".strip()

    def get_face_image_url(self):
        if self.face_image_path:
            try:
                return default_storage.url(self.face_image_path)
            except:
                return None
        return None


class SecurityEvent(models.Model):
    EVENT_TYPES = (
        ("face_recognized", "Rostro reconocido"),
        ("face_unknown", "Rostro desconocido"),
        ("dangerous_object", "Objeto peligroso detectado"),
        ("ppe_missing", "Falta de EPP"),
        ("ppe_incorrect", "Uso incorrecto de EPP"),
        ("camera_error", "Error de cámara"),
    )

    event_type = models.CharField(max_length=50, choices=EVENT_TYPES)
    details = models.TextField()
    image_path = models.CharField(max_length=500, blank=True, null=True)
    timestamp = models.DateTimeField(auto_now_add=True)
    resolved = models.BooleanField(default=False)
    related_user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )

    authorized_person = models.ForeignKey(
        'AuthorizedPerson',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='security_events'
    )

    camera = models.ForeignKey(
        'Camera',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='security_events'
    )

    class Meta:
        ordering = ["-timestamp"]
        verbose_name = "Evento de Seguridad"
        verbose_name_plural = "Eventos de Seguridad"

    def __str__(self):
        return f"{self.get_event_type_display()} - {self.timestamp.strftime('%Y-%m-%d %H:%M:%S')}"

    #def get_image_url(self):
    #    if self.image_path:
    #        return f"{settings.MEDIA_URL}{self.image_path}"
    #    return None

    def get_image_url(self):
        if self.image_path:
            try:
                return default_storage.url(self.image_path)
            except:
                return None
        return None


    def get_person_name(self):
        if self.related_user:
            full_name = self.related_user.get_full_name().strip()
            return full_name if full_name else self.related_user.username
        return "Desconocido"


class Camera(models.Model):
    nombre = models.CharField(max_length=100)
    source = models.CharField(max_length=500)
    ubicacion = models.CharField(max_length=200, blank=True, null=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Cámara'
        verbose_name_plural = 'Cámaras'
        ordering = ['nombre']

    def __str__(self):
        return self.nombre

    def get_video_source(self):
        source = str(self.source).strip()

        if source.isdigit():
            return int(source)

        return source
