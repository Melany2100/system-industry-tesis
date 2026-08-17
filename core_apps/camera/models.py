from django.db import models
from django.contrib.auth.models import User
from django.core.files.storage import default_storage
from django.utils import timezone


class AuthorizedPerson(models.Model):
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
        related_name="authorized_people_registered",
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
        return f"{self.nombres or ''} {self.apellidos or ''}".strip()

    def get_face_image_url(self):
        if self.face_image_path:
            try:
                return default_storage.url(self.face_image_path)
            except Exception:
                return None
        return None


class DetectionRule(models.Model):
    """
    Parametrización de objetos detectables.

    model_label debe coincidir con la etiqueta que devuelve el modelo.
    Ejemplos actuales de COCO: knife, scissors, bottle, cell phone, backpack.
    Ejemplos para modelo personalizado: box_cutter, oil_container, contact_cement.
    """

    CATEGORY_CHOICES = (
        ("dangerous", "Objeto peligroso"),
        ("unauthorized", "Objeto no autorizado"),
        ("animal", "Animal"),
        ("ppe", "EPP"),
        ("allowed", "Permitido / bajo riesgo"),
    )

    LEVEL_CHOICES = (
        ("CRITICO", "Crítico"),
        ("ALTO", "Alto"),
        ("MEDIO", "Medio"),
        ("BAJO", "Bajo"),
    )

    EVENT_TYPE_CHOICES = (
        ("authorized_object", "Objeto autorizado"),
        ("dangerous_object", "Objeto peligroso"),
        ("unauthorized_access", "Acceso no autorizado"),
    )

    name = models.CharField(max_length=100)
    model_label = models.CharField(
        max_length=100,
        unique=True,
        help_text="Nombre exacto que devuelve el modelo. Ejemplo: knife, cell phone, backpack.",
    )
    aliases = models.TextField(
        blank=True,
        null=True,
        help_text="Nombres alternativos separados por coma. Ejemplo: cuchillo, arma blanca.",
    )
    category = models.CharField(max_length=30, choices=CATEGORY_CHOICES)
    risk_level = models.CharField(max_length=20, choices=LEVEL_CHOICES)
    event_type = models.CharField(max_length=50, choices=EVENT_TYPE_CHOICES)
    should_alert = models.BooleanField(
        default=True,
        help_text="Si está activo, el evento se considera alerta visual. Si no, solo se registra.",
    )
    is_active = models.BooleanField(default=True)
    min_confidence = models.FloatField(
        default=0.50,
        help_text="Confianza mínima para aceptar la detección. Valor entre 0 y 1.",
    )
    cooldown_seconds = models.PositiveIntegerField(
        default=20,
        help_text="Tiempo mínimo entre eventos repetidos del mismo objeto.",
    )
    requires_duration = models.BooleanField(
        default=False,
        help_text="Usar cuando el objeto solo debe alertar después de permanecer cierto tiempo.",
    )
    max_allowed_seconds = models.PositiveIntegerField(
        default=0,
        help_text="Tiempo permitido antes de registrar el evento. Ejemplo: celular por más de 60 segundos.",
    )
    description = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Regla de detección"
        verbose_name_plural = "Reglas de detección"
        ordering = ["category", "risk_level", "name"]

    def __str__(self):
        alert_text = "Alerta" if self.should_alert else "Solo registro"
        return f"{self.name} - {self.risk_level} - {alert_text}"

    def get_aliases_list(self):
        if not self.aliases:
            return []
        return [alias.strip() for alias in self.aliases.split(",") if alias.strip()]


class SecurityEvent(models.Model):
    EMAIL_STATUS_CHOICES = (
        ('PENDING', 'Pendiente'),
        ('SENT', 'Enviado'),
        ('FAILED', 'Fallido'),
        ('SKIPPED', 'No aplica'),
    )
    EVENT_TYPES = (
        ('face_recognized', 'Rostro reconocido'),
        ('face_unknown', 'Rostro desconocido'),
        ('ppe_missing', 'Falta de EPP'),
        ('intrusion', 'Intrusión'),
        ('authorized_object', 'Objeto autorizado'),
        ('unauthorized_object', 'Objeto no autorizado'),
        ('dangerous_object', 'Objeto peligroso detectado'),
        ('fall_detected', 'Movimiento'),
        ('phone_usage', 'Distraccion'),
        ('collision_risk', 'Riesgo de choque'),
        ('cut_risk', 'Riesgo de corte'),
        ('unauthorized_access', 'Acceso no autorizado'),
    )
    SEVERITY_LEVELS = (
        ('BAJO', 'Bajo'),
        ('MEDIO', 'Medio'),
        ('ALTO', 'Alto'),
        ('CRITICO', 'Crítico'),
    )

    event_type = models.CharField(max_length=50, choices=EVENT_TYPES)
    severity = models.CharField(max_length=10, choices=SEVERITY_LEVELS, default='MEDIO')
    details = models.TextField()
    image_path = models.CharField(max_length=500, blank=True, null=True)
    timestamp = models.DateTimeField(auto_now_add=True)
    resolved = models.BooleanField(default=False)
    object_label = models.CharField(max_length=100, blank=True, null=True)
    should_alert = models.BooleanField(default=True)
    email_status = models.CharField(
        max_length=10,
        choices=EMAIL_STATUS_CHOICES,
        default='PENDING',
    )
    email_recipient = models.EmailField(blank=True, default='')
    email_cc = models.TextField(blank=True, default='')
    email_sent_at = models.DateTimeField(null=True, blank=True)
    email_error = models.TextField(blank=True, default='')
    related_user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="related_security_events",
    )
    authorized_person = models.ForeignKey(
        "AuthorizedPerson",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="security_events",
    )
    reviewed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_security_events",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    managed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="managed_security_events",
    )
    managed_at = models.DateTimeField(null=True, blank=True)
    camera = models.ForeignKey(
        "Camera",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="security_events",
    )

    # Campos estructurados para filtrar/reportar detecciones sin depender del texto details.
    category = models.CharField(max_length=30, blank=True, null=True)
    object_label = models.CharField(max_length=100, blank=True, null=True)
    risk_level = models.CharField(
        max_length=20,
        choices=DetectionRule.LEVEL_CHOICES,
        blank=True,
        null=True,
    )
    confidence = models.FloatField(blank=True, null=True)
    duration_seconds = models.PositiveIntegerField(default=0)
    should_alert = models.BooleanField(default=True)
    source_event_key = models.CharField(
        max_length=180,
        blank=True,
        null=True,
        unique=True,
        help_text="Identificador idempotente asignado por el nodo edge de origen.",
    )

    class Meta:
        ordering = ["-timestamp"]
        verbose_name = "Evento de Seguridad"
        verbose_name_plural = "Eventos de Seguridad"

    def __str__(self):
        local_timestamp = timezone.localtime(self.timestamp)
        return f"{self.get_event_type_display()} - {local_timestamp.strftime('%Y-%m-%d %H:%M:%S')}"

    def get_image_url(self):
        if self.image_path:
            try:
                return default_storage.url(self.image_path)
            except Exception:
                return None
        return None

    def get_person_name(self):
        if self.authorized_person:
            return self.authorized_person.get_full_name() or "Persona autorizada"
        if self.related_user:
            full_name = self.related_user.get_full_name().strip()
            return full_name if full_name else self.related_user.username

        return "Desconocido/a"


class EventSyncOutbox(models.Model):
    """Cola persistente de eventos pendientes de enviar al nodo cloud."""

    STATUS_PENDING = "PENDING"
    STATUS_RETRY = "RETRY"
    STATUS_SYNCED = "SYNCED"
    STATUS_CHOICES = (
        (STATUS_PENDING, "Pendiente"),
        (STATUS_RETRY, "Reintentar"),
        (STATUS_SYNCED, "Sincronizado"),
    )

    event = models.OneToOneField(
        SecurityEvent,
        on_delete=models.CASCADE,
        related_name="sync_outbox",
    )
    source_key = models.CharField(max_length=180, unique=True)
    status = models.CharField(
        max_length=10,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
    )
    attempts = models.PositiveIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now, db_index=True)
    last_error = models.TextField(blank=True, default="")
    synced_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("next_attempt_at", "id")
        verbose_name = "Evento pendiente de sincronización"
        verbose_name_plural = "Eventos pendientes de sincronización"

    def __str__(self):
        return f"{self.source_key} - {self.status}"


class DetectionFunction(models.Model):
    """Activa una regla de visión que ya está implementada en el sistema."""

    DETECTION_TYPES = (
        ('face_recognized', 'Rostro reconocido'),
        ('face_unknown', 'Rostro desconocido'),
        ('intrusion', 'Intrusión'),
        ('unauthorized_access', 'Acceso no autorizado'),
        ('phone_usage', 'Distracción por uso de celular'),
        ('ppe_missing', 'Falta de EPP'),
        ('fall_detected', 'Movimiento o caída'),
        ('authorized_object', 'Objeto autorizado'),
        ('unauthorized_object', 'Objeto no autorizado'),
        ('dangerous_object', 'Objeto peligroso detectado'),
        ('collision_risk', 'Riesgo de choque'),
        ('cut_risk', 'Riesgo de corte'),
    )

    name = models.CharField(
        'Nombre de la nueva categoría',
        max_length=120,
        help_text=(
            'Escribe una categoría propia, aunque todavía no exista en el sistema. '
            'Ejemplo: Prohibición de celulares en producción.'
        ),
    )
    event_type = models.CharField(
        'Regla de detección existente',
        max_length=50,
        choices=DETECTION_TYPES,
        unique=True,
        help_text='Selecciona la regla programada que ejecutará esta nueva categoría.',
    )
    severity = models.CharField(
        'Nivel de riesgo',
        max_length=10,
        choices=SecurityEvent.SEVERITY_LEVELS,
        default='MEDIO',
    )
    is_active = models.BooleanField('Activa', default=True)
    description = models.TextField(
        'Descripción',
        blank=True,
        help_text='Explica el objetivo operativo de la función.',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('name',)
        verbose_name = 'Función de detección'
        verbose_name_plural = 'Funciones de detección'

    def __str__(self):
        return f'{self.name} ({self.get_event_type_display()})'


class Camera(models.Model):
    nombre = models.CharField(max_length=100)
    source = models.CharField(max_length=500)
    stream_path = models.SlugField(
        max_length=120,
        blank=True,
        default="",
        help_text=(
            "Ruta publicada en MediaMTX, sin dominio ni barras. "
            "Si queda vacía se usará camera-ID."
        ),
    )
    ubicacion = models.CharField(max_length=200, blank=True, null=True)
    is_active = models.BooleanField(default=True)
    last_seen = models.DateTimeField(blank=True, null=True)

    class Meta:
        verbose_name = "Cámara"
        verbose_name_plural = "Cámaras"
        ordering = ["nombre"]

    def __str__(self):
        return self.nombre

    def get_video_source(self):
        source = str(self.source).strip()
        if source.isdigit():
            return int(source)
        return source

    def get_stream_path(self):
        """Devuelve una ruta estable para reproducir esta cámara en MediaMTX."""
        return self.stream_path.strip() or f"camera-{self.pk}"
