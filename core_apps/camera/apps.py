import os
import sys

from django.apps import AppConfig
from django.conf import settings


class CameraConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'core_apps.camera'

    def ready(self):
        if not getattr(settings, "SMRI_EDGE_ENABLED", False):
            return

        if not any(command in sys.argv for command in ("runserver", "runserver_plus")):
            return

        if "--noreload" not in sys.argv and os.environ.get("RUN_MAIN") != "true":
            return

        try:
            from .services.event_sync import start_event_sync_worker
            from .views import autostart_active_camera_workers, preload_camera_models

            start_event_sync_worker()
            preload_camera_models(async_load=True)

            if os.environ.get("SMRI_AUTOSTART_CAMERAS", "1") != "0":
                try:
                    target_fps = int(os.environ.get("SMRI_CAMERA_AUTOSTART_FPS", "8"))
                except ValueError:
                    target_fps = 8

                autostart_active_camera_workers(target_fps=target_fps)
        except Exception:
            pass
