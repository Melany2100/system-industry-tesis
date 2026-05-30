from django.urls import path
from . import views

urlpatterns = [
    path("settings/update-profile/", views.update_profile, name="settings_update_profile"),
    path("settings/update-email/", views.update_email, name="settings_update_email"),
    path("settings/update-password/", views.update_password, name="settings_update_password"),
    path("settings/update-appearance/", views.update_appearance, name="settings_update_appearance"),
    path("settings/update-notifications/", views.update_notifications, name="settings_update_notifications"),
    path("settings/update-security/", views.update_security, name="settings_update_security"),
]