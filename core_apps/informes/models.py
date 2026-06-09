# models.py
from django.db import models

class Informe(models.Model):
    fecha = models.DateTimeField(auto_now_add=True)
    camara = models.CharField(max_length=100)
    persona_detectada = models.CharField(max_length=100, blank=True, null=True)
    epp_correcto = models.BooleanField(default=False)
    descripcion = models.TextField(blank=True, null=True)
    evidencia = models.FileField(upload_to="informes/evidencias/", blank=True, null=True)
    security_event = models.ForeignKey(
        "camera.SecurityEvent",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="informes",
    )

    def __str__(self):
        return f"{self.persona_detectada} - {'EPP OK' if self.epp_correcto else 'Sin EPP'}"


