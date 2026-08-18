from django.urls import path
from . import views
from .views import CameraView, AlertaView, get_security_events, mark_event_as_resolved

urlpatterns = [
    path("", CameraView.as_view(), name="camera"),
    path("alerta/", AlertaView.as_view(), name="alerta"),

    path("video_feed/", views.video_feed_default, name="video_feed"),
    path("video_feed/<int:camera_id>/", views.video_feed, name="video_feed_camera"),
    
    path("live_status/", views.live_status, name="live_status"),
    path("api/v1/events/", views.ingest_edge_event, name="ingest_edge_event"),
    path(
        "api/v1/authorized-people/",
        views.authorized_people_manifest,
        name="authorized_people_manifest",
    ),
    path(
        "api/v1/authorized-people/<int:person_id>/image/",
        views.authorized_person_image,
        name="authorized_person_image",
    ),

    path("register_face/", views.register_face, name="register_face"),
    path("get_events/", views.get_events, name="get_events"),

    path("security-events/", get_security_events, name="get_security_events"),
    path("security-events/<int:event_id>/review/", views.review_security_event, name="review_security_event"),
    path("security-events/<int:event_id>/retry-email/", views.retry_incident_email, name="retry_incident_email"),
    path("security-events/<int:event_id>/resolve/", mark_event_as_resolved, name="mark_event_resolved"),

    path("status/<int:camera_id>/", views.camera_status, name="camera_status"),
    path("media-auth/", views.media_auth, name="media_auth"),

]
