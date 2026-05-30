from django.urls import path

from . import views

urlpatterns = [
    path("", views.CameraView.as_view(), name="camera"),
    path("alerta/", views.AlertaView.as_view(), name="alerta"),
    path("camera_status/", views.camera_status, name="camera_status"),
    path("video_feed/", views.video_feed, name="video_feed"),
    path("video_feed/<int:camera_id>/", views.video_feed, name="video_feed_camera"),
    
    path("live_status/", views.live_status, name="live_status"),
    path("register_face/", views.register_face, name="register_face"),
    path("get_events/", views.get_events, name="get_events"),
    path("security-events/", views.get_security_events, name="get_security_events"),
    path("security-events/<int:event_id>/resolve/", views.mark_event_resolved, name="mark_event_resolved"),
]