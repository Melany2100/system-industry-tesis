import os
import sys

from django.apps import AppConfig


class CameraConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'core_apps.camera'

    def ready(self):
        if not any(command in sys.argv for command in ("runserver", "runserver_plus")):
            return

        if "--noreload" not in sys.argv and os.environ.get("RUN_MAIN") != "true":
            return

        try:
            from .views import preload_camera_models

            preload_camera_models(async_load=True)
        except Exception:
            pass
