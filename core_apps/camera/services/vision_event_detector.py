import os
import time
from dataclasses import dataclass
from math import atan2, degrees
from pathlib import Path
from threading import Lock, Thread
from typing import List, Optional, Tuple

os.environ.setdefault(
    "YOLO_CONFIG_DIR",
    str(Path(__file__).resolve().parents[3] / ".ultralytics"),
)

import cv2
from django.conf import settings
from ultralytics import YOLO


@dataclass
class VisionEvent:
    event_type: str
    details: str
    category: str
    severity: str
    should_alert: bool
    box: Optional[Tuple[int, int, int, int]] = None
    object_label: Optional[str] = None
    confidence: Optional[float] = None
    person_name: Optional[str] = None
    authorized_person: object = None
    duration_seconds: Optional[float] = None


class VisionEventDetector:
    """
    Detecta eventos visuales del stream:
    - Objetos cortopunzantes: cuchillo/tijeras.
    - Posibles caidas por postura horizontal.
    - Uso prolongado de celular.
    """

    _object_model = None
    _pose_model = None
    _fast_model = None
    _load_lock = Lock()
    _inference_lock = Lock()

    def __init__(self):
        with self._load_lock:
            if VisionEventDetector._object_model is None:
                VisionEventDetector._object_model = YOLO(str(settings.YOLO_OBJECT_MODEL_PATH))

            if VisionEventDetector._pose_model is None:
                VisionEventDetector._pose_model = YOLO(str(settings.YOLO_POSE_MODEL_PATH))

            if VisionEventDetector._fast_model is None:
                if str(settings.YOLO_FAST_MODEL_PATH) == str(settings.YOLO_OBJECT_MODEL_PATH):
                    VisionEventDetector._fast_model = VisionEventDetector._object_model
                else:
                    VisionEventDetector._fast_model = YOLO(str(settings.YOLO_FAST_MODEL_PATH))

        self.object_model = VisionEventDetector._object_model
        self.pose_model = VisionEventDetector._pose_model
        self.fast_model = VisionEventDetector._fast_model

        self.frame_count = 0
        self.last_sharp_detections = []
        self.last_pose_events = []
        self.last_phone_boxes = []
        self.last_person_boxes = []
        self.last_person_boxes_at = 0.0
        self.phone_usage_start = None
        self.last_phone_seen = None
        self.last_event_times = {}
        self.sharp_detection_memory = {}
        self.fall_memory = {}
        self.phone_usage_memory = {}
        self.phone_area_memory = {}
        self._async_lock = Lock()
        self._async_busy = False
        self._latest_events = []
        self._pending_alert_events = []

    def submit(self, frame, identities=None) -> bool:
        with self._async_lock:
            if self._async_busy:
                return False

            self._async_busy = True

        frame_copy = frame.copy()
        identity_snapshot = list(identities or [])
        Thread(
            target=self._run_async_detect,
            args=(frame_copy, identity_snapshot),
            daemon=True,
        ).start()
        return True

    def get_latest(self):
        with self._async_lock:
            alert_events = list(self._pending_alert_events)
            self._pending_alert_events = []
            return list(self._latest_events), alert_events

    def _run_async_detect(self, frame, identities):
        try:
            events = self.detect(frame, identities=identities)
            alert_events = [event for event in events if event.should_alert]

            with self._async_lock:
                self._latest_events = events
                self._pending_alert_events.extend(alert_events)
        except Exception:
            with self._async_lock:
                self._latest_events = []
        finally:
            with self._async_lock:
                self._async_busy = False

    def _can_emit(self, event_key: str) -> bool:
        now = time.time()
        cooldown = getattr(settings, "EVENT_COOLDOWN_SECONDS", 10)
        last = self.last_event_times.get(event_key, 0)

        if now - last >= cooldown:
            self.last_event_times[event_key] = now
            return True

        return False

    def detect(self, frame, identities=None) -> List[VisionEvent]:
        self.frame_count += 1
        events = []
        identities = identities or []

        if self.frame_count % getattr(settings, "DETECT_SHARP_EVERY_N_FRAMES", 8) == 0:
            self.last_sharp_detections = self._detect_sharp_objects(frame)

        if self.frame_count % getattr(settings, "DETECT_POSE_EVERY_N_FRAMES", 10) == 0:
            self.last_pose_events = self._detect_fall(frame, identities=identities)

        if self.frame_count % getattr(settings, "DETECT_PHONE_EVERY_N_FRAMES", 3) == 0:
            events.extend(self._detect_phone_usage(frame, identities=identities))

        events.extend(self.last_sharp_detections)
        events.extend(self.last_pose_events)

        return events

    def _detect_sharp_objects(self, frame) -> List[VisionEvent]:
        events = []
        now = time.time()
        observed_keys = set()

        with self._inference_lock:
            results = self.object_model.predict(
                source=frame,
                conf=getattr(settings, "SHARP_OBJECT_CONF", 0.20),
                imgsz=getattr(settings, "SHARP_OBJECT_IMGSZ", 960),
                classes=getattr(settings, "SHARP_OBJECT_CLASSES", [43, 76]),
                verbose=False,
            )

        if not results or results[0].boxes is None:
            return events

        for box in results[0].boxes:
            cls_id = int(box.cls[0])
            label = self.object_model.names[cls_id]
            confidence = float(box.conf[0])
            x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
            alert_conf = self._sharp_alert_conf(label)

            if confidence < alert_conf:
                continue

            event_key = self._sharp_detection_key(label, (x1, y1, x2, y2))
            observed_keys.add(event_key)
            confirmation_count = self._track_sharp_detection(event_key, now)
            is_confirmed = confirmation_count >= getattr(
                settings,
                "SHARP_OBJECT_CONFIRMATION_FRAMES",
                2,
            )

            events.append(
                VisionEvent(
                    event_type="dangerous_object",
                    details=(
                        f"Objeto peligroso detectado: {label}. "
                        f"Posible riesgo de cortes o lesion por objeto cortopunzante. "
                        f"| Categoria: Posibles cortes | Nivel: CRITICO "
                        f"| Confianza: {confidence:.2f}"
                    ),
                    category="Posibles cortes",
                    severity="CRITICO",
                    should_alert=is_confirmed and self._can_emit(f"sharp:{label}"),
                    box=(x1, y1, x2, y2),
                    object_label=label.replace(" ", "_"),
                    confidence=confidence,
                )
            )

        self._prune_sharp_detections(observed_keys, now)
        return events

    def _detect_fall(self, frame, identities=None) -> List[VisionEvent]:
        events = []
        identities = identities or []

        with self._inference_lock:
            results = self.pose_model.predict(
                source=frame,
                conf=getattr(settings, "POSE_CONF", 0.35),
                imgsz=getattr(settings, "POSE_IMGSZ", 640),
                verbose=False,
            )

        if not results or results[0].boxes is None:
            self._prune_fall_memory(set(), time.time())
            return events

        observed_fall_keys = set()
        frame_height, frame_width = frame.shape[:2]
        min_box_area = frame_width * frame_height * getattr(
            settings,
            "FALL_MIN_BOX_AREA_RATIO",
            0.08,
        )
        keypoints = getattr(results[0], "keypoints", None)

        for index, box in enumerate(results[0].boxes):
            x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
            width = x2 - x1
            height = y2 - y1
            area = max(0, width) * max(0, height)

            if height <= 0 or area < min_box_area:
                continue

            person_box = (x1, y1, x2, y2)
            person_keypoints = self._keypoints_for_person(keypoints, index)

            if not self._is_fall_pose(
                person_box=person_box,
                keypoints=person_keypoints,
                frame_shape=frame.shape,
                min_box_area=min_box_area,
            ):
                continue

            identity = self._identity_for_box(person_box, identities)
            person_name = self._identity_name(identity)
            fall_key = self._person_event_key("fall", person_box, identity)
            observed_fall_keys.add(fall_key)
            elapsed = self._track_fall(fall_key, time.time())

            if elapsed >= getattr(settings, "FALL_SECONDS", 2.0):
                events.append(
                    VisionEvent(
                        event_type="fall_detected",
                        details=(
                            f"Movimiento detectado: {person_name} permanece en el piso "
                            f"o con postura inclinada durante {elapsed:.1f} segundos."
                        ),
                        category="Evento fisico",
                        severity="CRITICO",
                        should_alert=self._can_emit(f"fall_detected:{fall_key}"),
                        box=person_box,
                        object_label="person",
                        person_name=person_name,
                        authorized_person=self._identity_person(identity),
                    )
                )

        self._prune_fall_memory(observed_fall_keys, time.time())
        return events

    def _detect_phone_usage(self, frame, identities=None) -> List[VisionEvent]:
        now = time.time()
        identities = identities or []
        events = []

        with self._inference_lock:
            results = self.fast_model.predict(
                source=frame,
                conf=getattr(settings, "PHONE_CONF", 0.25),
                imgsz=getattr(settings, "PHONE_IMGSZ", 640),
                classes=getattr(settings, "PHONE_CLASSES", [0, 67]),
                verbose=False,
            )

        person_boxes = []
        phone_boxes = []

        if results and results[0].boxes is not None:
            for box in results[0].boxes:
                cls_id = int(box.cls[0])
                confidence = float(box.conf[0])
                x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())

                if cls_id == 0:
                    person_boxes.append((x1, y1, x2, y2, confidence))
                elif cls_id == 67:
                    phone_boxes.append((x1, y1, x2, y2, confidence))

        self.last_person_boxes = person_boxes
        self.last_person_boxes_at = time.monotonic()

        observed_phone_keys = set()
        matched_phone_indexes = set()

        for phone_index, phone in enumerate(phone_boxes):
            phone_box = phone[:4]
            phone_center = self._box_center(phone_box)

            for person in person_boxes:
                person_box = person[:4]
                expanded_person_box = self._expand_box(
                    person_box,
                    getattr(settings, "PHONE_PERSON_BOX_EXPAND_RATIO", 0.18),
                )

                if self._point_inside_box(phone_center, expanded_person_box):
                    matched_phone_indexes.add(phone_index)
                    identity = self._identity_for_box(person_box, identities)
                    person_name = self._identity_name(identity)
                    event_key = self._person_event_key("phone", person_box, identity)
                    observed_phone_keys.add(event_key)
                    elapsed = self._track_phone_usage(event_key, now)
                    alert_seconds = self._phone_alert_seconds(identity)
                    should_alert = elapsed >= alert_seconds
                    details = (
                        f"Uso de celular detectado durante {elapsed:.1f} segundos. "
                        f"Persona asociada: {person_name}."
                    )

                    events.append(
                        VisionEvent(
                            event_type="phone_usage",
                            details=details,
                            category="Distraccion",
                            severity="MEDIO",
                            should_alert=(
                                should_alert
                                and self._can_emit(f"phone_usage:{event_key}")
                            ),
                            box=phone_box,
                            object_label="cell_phone",
                            confidence=phone[4],
                            person_name=person_name,
                            authorized_person=self._identity_person(identity),
                            duration_seconds=elapsed,
                        )
                    )
                    break

        for phone_index, phone in enumerate(phone_boxes):
            if phone_index in matched_phone_indexes:
                continue

            if getattr(settings, "PHONE_AREA_REQUIRES_PERSON", True):
                continue

            phone_box = phone[:4]
            if phone[4] < getattr(settings, "PHONE_AREA_CONF", 0.65):
                continue

            event_key = self._phone_area_event_key(phone_box)
            observed_phone_keys.add(event_key)
            elapsed = self._track_phone_usage(event_key, now)
            area_count = self._track_phone_area(event_key, now)
            alert_seconds = getattr(settings, "PHONE_AREA_ALERT_SECONDS", 0)
            should_alert = (
                elapsed >= alert_seconds
                and area_count >= getattr(settings, "PHONE_AREA_CONFIRMATION_FRAMES", 2)
            )

            events.append(
                VisionEvent(
                    event_type="phone_usage",
                    details=(
                        "Uso de celular detectado en el area monitoreada sin persona "
                        f"identificada asociada durante {elapsed:.1f} segundos."
                    ),
                    category="Distraccion",
                    severity="MEDIO",
                    should_alert=(
                        should_alert
                        and self._can_emit(f"phone_usage:{event_key}")
                    ),
                    box=phone_box,
                    object_label="cell_phone",
                    confidence=phone[4],
                    person_name="Persona no identificada",
                    duration_seconds=elapsed,
                )
            )

        self.last_phone_boxes = [
            phone
            for phone_index, phone in enumerate(phone_boxes)
            if phone_index in matched_phone_indexes
        ]
        self._prune_phone_usage(observed_phone_keys, now)
        self._prune_phone_area(observed_phone_keys, now)
        return events

    def draw(self, frame, events: List[VisionEvent]):
        for event in events:
            if not event.box:
                continue

            x1, y1, x2, y2 = event.box
            color = (0, 0, 255) if event.severity == "CRITICO" else (0, 165, 255)

            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
            cv2.putText(
                frame,
                f"{event.event_type} - {event.severity}",
                (x1, max(y1 - 10, 20)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                color,
                2,
            )

        for person in self.last_person_boxes:
            x1, y1, x2, y2, conf = person
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 180, 0), 2)
            cv2.putText(
                frame,
                f"person {conf:.2f}",
                (x1, max(y1 - 10, 20)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (0, 180, 0),
                2,
            )

        for phone in self.last_phone_boxes:
            x1, y1, x2, y2, conf = phone
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 165, 255), 2)
            cv2.putText(
                frame,
                f"cell_phone {conf:.2f}",
                (x1, max(y1 - 10, 20)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (0, 165, 255),
                2,
            )

        elapsed = self._max_phone_usage_elapsed()
        cv2.putText(
            frame,
            f"Uso celular: {elapsed:.1f}s",
            (20, 40),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (0, 0, 255) if elapsed > 0 else (255, 255, 255),
            2,
        )

        return frame

    def _box_center(self, box):
        x1, y1, x2, y2 = box
        return ((x1 + x2) / 2, (y1 + y2) / 2)

    def _point_inside_box(self, point, box):
        px, py = point
        x1, y1, x2, y2 = box
        return x1 <= px <= x2 and y1 <= py <= y2

    def _expand_box(self, box, ratio):
        x1, y1, x2, y2 = box
        width = x2 - x1
        height = y2 - y1
        pad_x = int(width * ratio)
        pad_y = int(height * ratio)
        return x1 - pad_x, y1 - pad_y, x2 + pad_x, y2 + pad_y

    def _sharp_alert_conf(self, label):
        thresholds = getattr(settings, "SHARP_OBJECT_ALERT_CONF", {})

        if isinstance(thresholds, dict):
            return float(thresholds.get(label, 0.35))

        return float(thresholds or 0.35)

    def _sharp_detection_key(self, label, box):
        center_x, center_y = self._box_center(box)
        return f"{label}:{int(center_x // 80)}:{int(center_y // 80)}"

    def _track_sharp_detection(self, key, now):
        entry = self.sharp_detection_memory.get(key, {"count": 0})
        entry["count"] += 1
        entry["last_seen"] = now
        self.sharp_detection_memory[key] = entry

        return entry["count"]

    def _prune_sharp_detections(self, observed_keys, now):
        ttl_seconds = getattr(settings, "SHARP_OBJECT_TTL_SECONDS", 2.0)

        for key in list(self.sharp_detection_memory.keys()):
            last_seen = self.sharp_detection_memory[key].get("last_seen", 0)

            if key not in observed_keys and now - last_seen > ttl_seconds:
                del self.sharp_detection_memory[key]

    def _keypoints_for_person(self, keypoints, index):
        if keypoints is None or getattr(keypoints, "xy", None) is None:
            return None

        try:
            xy = keypoints.xy[index].tolist()
            conf = keypoints.conf[index].tolist() if keypoints.conf is not None else None
            return {"xy": xy, "conf": conf}
        except Exception:
            return None

    def _is_fall_pose(self, person_box, keypoints, frame_shape, min_box_area):
        x1, y1, x2, y2 = person_box
        width = max(0, x2 - x1)
        height = max(0, y2 - y1)
        area = width * height

        if height <= 0 or area < min_box_area:
            return False

        aspect_ratio = width / height
        if aspect_ratio >= getattr(settings, "FALL_ASPECT_RATIO", 1.60):
            return True

        torso_angle = self._torso_angle_from_vertical(keypoints)
        if torso_angle is None:
            return False

        if torso_angle < getattr(settings, "FALL_TORSO_ANGLE", 65):
            return False

        _, frame_width = frame_shape[:2]
        horizontal_spread_ratio = width / max(1, frame_width)

        return horizontal_spread_ratio >= getattr(settings, "FALL_MIN_WIDTH_RATIO", 0.28)

    def _torso_angle_from_vertical(self, keypoints):
        if not keypoints:
            return None

        shoulder = self._midpoint_from_keypoints(keypoints, 5, 6)
        hip = self._midpoint_from_keypoints(keypoints, 11, 12)

        if shoulder is None or hip is None:
            return None

        dx = hip[0] - shoulder[0]
        dy = hip[1] - shoulder[1]

        if abs(dx) + abs(dy) <= 1:
            return None

        return degrees(atan2(abs(dx), abs(dy)))

    def _midpoint_from_keypoints(self, keypoints, left_index, right_index):
        points = []

        for idx in (left_index, right_index):
            point = self._valid_keypoint(keypoints, idx)

            if point is not None:
                points.append(point)

        if not points:
            return None

        return (
            sum(point[0] for point in points) / len(points),
            sum(point[1] for point in points) / len(points),
        )

    def _valid_keypoint(self, keypoints, index):
        xy = keypoints.get("xy") or []
        conf = keypoints.get("conf")

        if index >= len(xy):
            return None

        if conf is not None and index < len(conf):
            if conf[index] < getattr(settings, "POSE_KEYPOINT_CONF", 0.25):
                return None

        x, y = xy[index][:2]

        if x <= 0 and y <= 0:
            return None

        return x, y

    def _track_fall(self, key, now):
        entry = self.fall_memory.get(key)

        if entry is None:
            entry = {"first_seen": now, "last_seen": now}
            self.fall_memory[key] = entry
        else:
            entry["last_seen"] = now

        return now - entry["first_seen"]

    def _prune_fall_memory(self, observed_keys, now):
        ttl_seconds = getattr(settings, "FALL_TRACK_TTL_SECONDS", 3.0)

        for key in list(self.fall_memory.keys()):
            last_seen = self.fall_memory[key].get("last_seen", 0)

            if key not in observed_keys and now - last_seen > ttl_seconds:
                del self.fall_memory[key]

    def _track_phone_usage(self, key, now):
        entry = self.phone_usage_memory.get(key)

        if entry is None:
            entry = {"first_seen": now, "last_seen": now}
            self.phone_usage_memory[key] = entry
        else:
            entry["last_seen"] = now

        return now - entry["first_seen"]

    def _prune_phone_usage(self, observed_keys, now):
        ttl_seconds = getattr(settings, "PHONE_TRACK_TTL_SECONDS", 2.5)

        for key in list(self.phone_usage_memory.keys()):
            last_seen = self.phone_usage_memory[key].get("last_seen", 0)

            if key not in observed_keys and now - last_seen > ttl_seconds:
                del self.phone_usage_memory[key]

    def _track_phone_area(self, key, now):
        entry = self.phone_area_memory.get(key, {"count": 0})
        entry["count"] += 1
        entry["last_seen"] = now
        self.phone_area_memory[key] = entry

        return entry["count"]

    def _prune_phone_area(self, observed_keys, now):
        ttl_seconds = getattr(settings, "PHONE_TRACK_TTL_SECONDS", 2.5)

        for key in list(self.phone_area_memory.keys()):
            last_seen = self.phone_area_memory[key].get("last_seen", 0)

            if key not in observed_keys and now - last_seen > ttl_seconds:
                del self.phone_area_memory[key]

    def _max_phone_usage_elapsed(self):
        if not self.phone_usage_memory:
            return 0

        now = time.time()
        return max(
            now - entry.get("first_seen", now)
            for entry in self.phone_usage_memory.values()
        )

    def _identity_for_box(self, box, identities):
        if not box:
            return None

        x1, y1, x2, y2 = box
        best_identity = None
        best_score = 0.0

        for identity in identities:
            identity_box = identity.get("coords")

            if not identity_box:
                continue

            overlap = self._intersection_area(box, identity_box)
            identity_area = self._box_area(identity_box)
            score = overlap / float(identity_area) if identity_area > 0 else 0

            if score > best_score:
                best_score = score
                best_identity = identity

        if best_identity is not None and best_score >= 0.10:
            return best_identity

        center = self._box_center(box)

        for identity in identities:
            identity_box = identity.get("coords")

            if identity_box and self._point_inside_box(center, identity_box):
                return identity

        return None

    def _identity_name(self, identity):
        if not identity:
            return "Persona no identificada"

        if identity.get("is_authorized"):
            return identity.get("name") or "Persona autorizada"

        return "Persona no autorizada"

    def _identity_person(self, identity):
        if identity and identity.get("is_authorized"):
            return identity.get("person_obj")

        return None

    def _phone_alert_seconds(self, identity):
        if not identity:
            return getattr(settings, "PHONE_UNIDENTIFIED_ALERT_SECONDS", 0)

        if not identity.get("is_authorized"):
            return getattr(settings, "PHONE_UNAUTHORIZED_ALERT_SECONDS", 0)

        return getattr(settings, "PHONE_ALERT_SECONDS", 10)

    def _person_event_key(self, prefix, box, identity):
        person = self._identity_person(identity)

        if person is not None:
            return f"{prefix}:person:{person.id}"

        if not box:
            return f"{prefix}:unknown"

        center_x, center_y = self._box_center(box)
        return f"{prefix}:unknown:{int(center_x // 120)}:{int(center_y // 120)}"

    def _phone_area_event_key(self, box):
        center_x, center_y = self._box_center(box)
        return f"phone:area:{int(center_x // 120)}:{int(center_y // 120)}"

    def _box_area(self, box):
        x1, y1, x2, y2 = box
        return max(0, x2 - x1) * max(0, y2 - y1)

    def _intersection_area(self, box_a, box_b):
        ax1, ay1, ax2, ay2 = box_a
        bx1, by1, bx2, by2 = box_b
        ix1 = max(ax1, bx1)
        iy1 = max(ay1, by1)
        ix2 = min(ax2, bx2)
        iy2 = min(ay2, by2)
        return max(0, ix2 - ix1) * max(0, iy2 - iy1)


_vision_event_detectors = {}
_vision_detector_lock = Lock()


def get_vision_event_detector(key="default"):
    with _vision_detector_lock:
        if key not in _vision_event_detectors:
            _vision_event_detectors[key] = VisionEventDetector()

        return _vision_event_detectors[key]
