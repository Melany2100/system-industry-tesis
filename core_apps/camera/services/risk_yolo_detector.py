from dataclasses import dataclass
import os
from pathlib import Path
from threading import Lock
from typing import List, Tuple

os.environ.setdefault(
    "YOLO_CONFIG_DIR",
    str(Path(__file__).resolve().parents[3] / ".ultralytics"),
)

import cv2
from django.conf import settings
from ultralytics import YOLO


@dataclass
class RiskDetection:
    label: str
    internal_label: str
    confidence: float
    box: Tuple[int, int, int, int]
    category: str
    severity: str
    event_type: str
    message: str
    color: Tuple[int, int, int]
    should_alert: bool


COCO_TO_INTERNAL = {
    "knife": "knife",
    "scissors": "scissors",
    "cell phone": "cell_phone",
    "backpack": "backpack",
    "handbag": "handbag",
    "suitcase": "suitcase",
    "dog": "dog",
    "cat": "cat",
    "bird": "bird",
    "bottle": "bottle",
}

RISK_RULES = {
    "knife": {
        "category": "Objeto peligroso",
        "severity": "CRITICO",
        "event_type": "dangerous_object",
        "message": "Objeto peligroso detectado: cuchillo",
        "color": (0, 0, 255),
        "should_alert": True,
    },
    "scissors": {
        "category": "Objeto peligroso",
        "severity": "CRITICO",
        "event_type": "dangerous_object",
        "message": "Objeto peligroso detectado: tijeras",
        "color": (0, 0, 255),
        "should_alert": True,
    },
    "cell_phone": {
        "category": "No autorizado",
        "severity": "ALTO",
        "event_type": "unauthorized_object",
        "message": "Objeto no autorizado detectado: celular",
        "color": (0, 165, 255),
        "should_alert": True,
    },
    "backpack": {
        "category": "No autorizado",
        "severity": "ALTO",
        "event_type": "unauthorized_object",
        "message": "Objeto no autorizado detectado: mochila",
        "color": (0, 165, 255),
        "should_alert": True,
    },
    "handbag": {
        "category": "No autorizado",
        "severity": "ALTO",
        "event_type": "unauthorized_object",
        "message": "Objeto no autorizado detectado: bolso",
        "color": (0, 165, 255),
        "should_alert": True,
    },
    "suitcase": {
        "category": "No autorizado",
        "severity": "ALTO",
        "event_type": "unauthorized_object",
        "message": "Objeto no autorizado detectado: maleta",
        "color": (0, 165, 255),
        "should_alert": True,
    },
    "dog": {
        "category": "Acceso no autorizado",
        "severity": "ALTO",
        "event_type": "unauthorized_access",
        "message": "Acceso no autorizado: animal en el area monitoreada (posible perro/gato)",
        "color": (0, 165, 255),
        "should_alert": True,
    },
    "cat": {
        "category": "Acceso no autorizado",
        "severity": "ALTO",
        "event_type": "unauthorized_access",
        "message": "Acceso no autorizado: gato en el area monitoreada",
        "color": (0, 165, 255),
        "should_alert": True,
    },
    "bird": {
        "category": "Acceso no autorizado",
        "severity": "ALTO",
        "event_type": "unauthorized_access",
        "message": "Acceso no autorizado: ave en el area monitoreada",
        "color": (0, 165, 255),
        "should_alert": True,
    },
    "bottle": {
        "category": "Objeto no autorizado",
        "severity": "MEDIO",
        "event_type": "dangerous_object",
        "message": "Botella detectada en zona monitoreada",
        "color": (0, 255, 255),
        "should_alert": False,
    },
}


class RiskYoloDetector:
    _model = None
    _load_lock = Lock()
    _inference_lock = Lock()

    def __init__(self):
        self.model = self._load_model()
        self.conf = getattr(settings, "RISK_YOLO_CONF", 0.55)
        self.imgsz = getattr(settings, "RISK_YOLO_IMGSZ", 640)
        self.classes = getattr(settings, "RISK_YOLO_CLASSES", None)

    @classmethod
    def _load_model(cls):
        with cls._load_lock:
            if cls._model is None:
                cls._model = YOLO(str(settings.RISK_YOLO_MODEL_PATH))
            return cls._model

    def detect(self, frame) -> List[RiskDetection]:
        detections = []

        with self._inference_lock:
            results = self.model.predict(
                source=frame,
                conf=self.conf,
                imgsz=self.imgsz,
                classes=self.classes,
                verbose=False,
            )

        if not results or results[0].boxes is None:
            return detections

        for box in results[0].boxes:
            cls_id = int(box.cls[0])
            coco_label = self.model.names[cls_id]
            internal_label = COCO_TO_INTERNAL.get(coco_label)

            if not internal_label:
                continue

            rule = RISK_RULES.get(internal_label)

            if not rule:
                continue

            x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
            confidence = float(box.conf[0])
            alert_conf = getattr(settings, "RISK_YOLO_ALERT_CONF", {}).get(
                internal_label,
                self.conf,
            )

            if confidence < alert_conf:
                continue

            detections.append(
                RiskDetection(
                    label=coco_label,
                    internal_label=internal_label,
                    confidence=confidence,
                    box=(x1, y1, x2, y2),
                    category=rule["category"],
                    severity=rule["severity"],
                    event_type=rule["event_type"],
                    message=rule["message"],
                    color=rule["color"],
                    should_alert=rule["should_alert"],
                )
            )

        return detections

    def draw_detections(self, frame, detections: List[RiskDetection]):
        for detection in detections:
            x1, y1, x2, y2 = detection.box
            text = (
                f"{detection.internal_label}: "
                f"{detection.confidence:.2f} | {detection.severity}"
            )

            cv2.rectangle(frame, (x1, y1), (x2, y2), detection.color, 2)
            cv2.putText(
                frame,
                text,
                (x1, max(y1 - 10, 20)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                detection.color,
                2,
            )

        return frame


_risk_yolo_detector = None
_risk_detector_lock = Lock()


def get_risk_yolo_detector():
    global _risk_yolo_detector

    with _risk_detector_lock:
        if _risk_yolo_detector is None:
            _risk_yolo_detector = RiskYoloDetector()

        return _risk_yolo_detector
