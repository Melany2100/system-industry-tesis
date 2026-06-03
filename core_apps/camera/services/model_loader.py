from __future__ import annotations

import os

from django.conf import settings

from .live_log import log_line

YOLO_CONFIG = {
    "weights": os.path.join(settings.BASE_DIR, "camera", "yolov3-tiny.weights"),
    "cfg": os.path.join(settings.BASE_DIR, "camera", "yolov3-tiny.cfg"),
    "classes": os.path.join(settings.BASE_DIR, "camera", "coco.names"),
    "dangerous_classes": ["knife", "gun", "scissors", "bottle"],
}

_YOLO_CACHE = {"net": None, "classes": None}
_PPE_CACHE = {"model": None}


def safe_import_cv2():
    try:
        import cv2  # type: ignore
        return cv2
    except Exception:
        return None


def safe_import_numpy():
    try:
        import numpy as np  # type: ignore
        return np
    except Exception:
        return None


def safe_import_face_recognition():
    try:
        import face_recognition  # type: ignore
        return face_recognition
    except Exception:
        return None


def safe_import_ultralytics():
    try:
        from ultralytics import YOLO  # type: ignore
        return YOLO
    except Exception:
        return None


def load_yolo():
    if _YOLO_CACHE["net"] is not None and _YOLO_CACHE["classes"] is not None:
        return _YOLO_CACHE["net"], _YOLO_CACHE["classes"]

    cv2 = safe_import_cv2()
    if cv2 is None:
        log_line("OpenCV no disponible: YOLO deshabilitado", key="cv2_missing", throttle_sec=10)
        return None, None

    if not (
        os.path.exists(YOLO_CONFIG["weights"])
        and os.path.exists(YOLO_CONFIG["cfg"])
        and os.path.exists(YOLO_CONFIG["classes"])
    ):
        log_line("Archivos YOLO no encontrados (weights/cfg/classes)", key="yolo_files_missing", throttle_sec=10)
        return None, None

    try:
        net = cv2.dnn.readNet(YOLO_CONFIG["weights"], YOLO_CONFIG["cfg"])
        with open(YOLO_CONFIG["classes"], "r", encoding="utf-8") as f:
            classes = [line.strip() for line in f.readlines()]

        _YOLO_CACHE["net"] = net
        _YOLO_CACHE["classes"] = classes
        log_line("✅ YOLO cargado", key="yolo_loaded", throttle_sec=10)
        return net, classes
    except Exception as e:
        log_line(f"❌ Error cargando YOLO: {e}", key="yolo_load_err", throttle_sec=10)
        return None, None


def load_ppe_model():
    if _PPE_CACHE["model"] is not None:
        return _PPE_CACHE["model"]

    YOLO = safe_import_ultralytics()
    if YOLO is None:
        log_line("Ultralytics no disponible: PPE deshabilitado", key="ultra_missing", throttle_sec=10)
        return None

    model_path = os.path.join(settings.BASE_DIR, "camera", "ppe.pt")
    if not os.path.exists(model_path):
        log_line(f"❌ No existe ppe.pt en: {model_path}", key="ppe_file_missing", throttle_sec=10)
        return None

    try:
        model = YOLO(model_path)
        _PPE_CACHE["model"] = model
        log_line("✅ PPE model cargado", key="ppe_loaded", throttle_sec=10)
        return model
    except Exception as e:
        log_line(f"❌ Error cargando PPE model: {e}", key="ppe_load_err", throttle_sec=10)
        return None