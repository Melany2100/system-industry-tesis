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
            from .views import autostart_active_camera_workers, preload_camera_models

            preload_camera_models(async_load=True)

            if os.environ.get("SMRI_AUTOSTART_CAMERAS") == "1":
                try:
                    target_fps = int(os.environ.get("SMRI_CAMERA_AUTOSTART_FPS", "8"))
                except ValueError:
                    target_fps = 8

                autostart_active_camera_workers(target_fps=target_fps)
        except Exception:
            pass
