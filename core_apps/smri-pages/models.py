from django.db import models
from django.contrib.auth.models import User


class UserSetting(models.Model):
    COLOR_CHOICES = (
        ('verde', 'Verde'),
        ('azul', 'Azul'),
        ('morado', 'Morado'),
        ('rojo', 'Rojo'),
    )

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='settings_profile')

    theme_color = models.CharField(max_length=20, choices=COLOR_CHOICES, default='azul')
    compact_layout = models.BooleanField(default=False)
    dark_mode = models.BooleanField(default=False)

    security_alerts = models.BooleanField(default=True)
    email_alerts = models.BooleanField(default=False)

    extra_verification = models.BooleanField(default=False)

    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Configuración de {self.user.username}"