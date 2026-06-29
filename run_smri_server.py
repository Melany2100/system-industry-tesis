import os
import sys
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

os.chdir(BASE_DIR)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("SMRI_AUTOSTART_CAMERAS", "1")
os.environ.setdefault("SMRI_CAMERA_AUTOSTART_FPS", "2")

stdout_log = open(LOG_DIR / "smri_server_stdout.log", "a", buffering=1, encoding="utf-8")
stderr_log = open(LOG_DIR / "smri_server_stderr.log", "a", buffering=1, encoding="utf-8")
sys.stdout = stdout_log
sys.stderr = stderr_log

print("=" * 60)
print("[SMRI] Servidor Django iniciado desde run_smri_server.py")
print(f"[SMRI] Proyecto: {BASE_DIR}")

from django.core.management import execute_from_command_line

execute_from_command_line([
    str(BASE_DIR / "manage.py"),
    "runserver",
    "0.0.0.0:8000",
    "--noreload",
])
