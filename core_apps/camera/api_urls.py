from django.urls import path
from .api_views import receive_detection_event

urlpatterns = [
    path("events/receive/", receive_detection_event, name="receive_detection_event"),
]
