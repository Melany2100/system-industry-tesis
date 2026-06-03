from django.urls import path
from . import views

urlpatterns = [
    path("", views.CameraView.as_view(), name="camera"),
    path("alerta/", views.AlertaView.as_view(), name="alerta"),

    # Mantener cámara por defecto
    path("video_feed/", views.video_feed_default, name="video_feed"),

    # Mantener selección de cámaras del template de Melany
    path("video_feed/<int:camera_id>/", views.video_feed_camera, name="video_feed_camera"),

    path("live_status/", views.live_status, name="live_status"),
    path("register_face/", views.register_face, name="register_face"),
    path("get_events/", views.get_events, name="get_events"),
    path("security-events/", views.get_security_events, name="get_security_events"),
    path(
        "security-events/<int:event_id>/resolve/",
        views.mark_event_resolved,
        name="mark_event_resolved",
    ),
]