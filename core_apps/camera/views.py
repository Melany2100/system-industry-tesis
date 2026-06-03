from core_apps.camera.utils import cv2

import os
import json
import time
import traceback
from collections import deque
from threading import Lock

from django.conf import settings
from django.http import JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404
from django.views.decorators import gzip
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import TemplateView

from .models import AuthorizedPerson, SecurityEvent, Camera
from core_apps.camera.utils import create_security_event, can_save_event, save_authorized_face_image

# =========================
# LIVE LOG (RAM) - incremental
# =========================
_LIVE_LOG = deque(maxlen=300)
_LOG_LOCK = Lock()
_LOG_SEQ = 0
_LAST_LOG_TS: dict[str, float] = {}



# =========================
# Helpers de identidad y asociación rostro/persona
# =========================
FACE_MEMORY_SECONDS = 6.0
FACE_MATCH_DISTANCE = 90.0
FACE_OVERLAP_THRESHOLD = 0.20

ANIMAL_CLASSES = {"cat", "dog", "bird"}
DEFAULT_OBJECT_CONFIDENCE = 0.35
ANIMAL_OBJECT_CONFIDENCE = 0.20


def _safe_event_key(value: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in str(value).lower())


def _box_area(box) -> int:
    x1, y1, x2, y2 = box
    return max(0, x2 - x1) * max(0, y2 - y1)


def _intersection_area(box_a, box_b) -> int:
    ax1, ay1, ax2, ay2 = box_a
    bx1, by1, bx2, by2 = box_b

    ix1 = max(ax1, bx1)
    iy1 = max(ay1, by1)
    ix2 = min(ax2, bx2)
    iy2 = min(ay2, by2)

    return max(0, ix2 - ix1) * max(0, iy2 - iy1)


def _box_center(box):
    x1, y1, x2, y2 = box
    return (x1 + x2) // 2, (y1 + y2) // 2


def _match_identity_to_person_box(person_box, detected_faces):
    """
    Relaciona la persona detectada por PPE con un rostro reconocido.

    Primero intenta por solapamiento.
    Luego intenta por centro del rostro.
    """
    px1, py1, px2, py2 = person_box

    best_face = None
    best_score = 0.0

    for face in detected_faces:
        face_box = face.get("coords")

        if not face_box:
            continue

        face_area = _box_area(face_box)

        if face_area <= 0:
            continue

        inter_area = _intersection_area(person_box, face_box)
        overlap_ratio = inter_area / float(face_area)

        if overlap_ratio >= FACE_OVERLAP_THRESHOLD and overlap_ratio > best_score:
            best_score = overlap_ratio
            best_face = face

    if best_face is not None:
        return best_face

    # Fallback: centro del rostro dentro de la caja de persona
    for face in detected_faces:
        face_box = face.get("coords")

        if not face_box:
            continue

        face_cx, face_cy = _box_center(face_box)

        if px1 <= face_cx <= px2 and py1 <= face_cy <= py2:
            return face

    return None


def _merge_recent_faces(old_faces, new_faces, now):
    """
    Mantiene en memoria rostros recientes, aunque face_recognition
    no los vea en un frame específico.
    """
    merged_faces = list(new_faces)

    for old_face in old_faces:
        last_seen = old_face.get("last_seen", 0.0)

        if now - last_seen > FACE_MEMORY_SECONDS:
            continue

        old_center = old_face.get("center")

        if not old_center:
            continue

        already_matched = False

        for new_face in new_faces:
            new_center = new_face.get("center")

            if not new_center:
                continue

            dx = new_center[0] - old_center[0]
            dy = new_center[1] - old_center[1]
            dist = (dx ** 2 + dy ** 2) ** 0.5

            if dist < FACE_MATCH_DISTANCE:
                already_matched = True
                break

        if not already_matched:
            merged_faces.append(old_face)

    return merged_faces


def _get_recent_identity(detected_faces, now):
    """
    Fallback para cuando PPE no logra asociar por caja.
    Si hay una persona en pantalla y recientemente se reconoció un rostro,
    usa esa identidad.
    """
    recent_faces = [
        face for face in detected_faces
        if now - face.get("last_seen", 0.0) <= FACE_MEMORY_SECONDS
    ]

    if not recent_faces:
        return None

    authorized_faces = [
        face for face in recent_faces
        if face.get("is_authorized")
    ]

    if authorized_faces:
        return max(
            authorized_faces,
            key=lambda face: face.get("last_seen", 0.0)
        )

    return max(
        recent_faces,
        key=lambda face: face.get("last_seen", 0.0)
    )


def _get_identity_text(identity):
    if identity is None:
        return "Persona no identificada", "No autorizado", None

    if identity.get("is_authorized"):
        return (
            identity.get("name", "Persona autorizada"),
            "Autorizado",
            identity.get("person_obj"),
        )

    return "Desconocido", "No autorizado", None

def _log_line(message: str, key: str | None = None, throttle_sec: float = 0.0) -> None:
    global _LOG_SEQ
    now = time.monotonic()

    if key and throttle_sec > 0:
        last = _LAST_LOG_TS.get(key, 0.0)
        if (now - last) < throttle_sec:
            return
        _LAST_LOG_TS[key] = now

    ts = time.strftime("%H:%M:%S")

    with _LOG_LOCK:
        _LOG_SEQ += 1
        _LIVE_LOG.append({"id": _LOG_SEQ, "ts": ts, "msg": message})


def live_status(request):
    """Devuelve logs nuevos usando ?after=<id>"""
    try:
        after = int(request.GET.get("after", "0"))
    except ValueError:
        after = 0

    with _LOG_LOCK:
        last_id = _LOG_SEQ
        lines = [x for x in _LIVE_LOG if x["id"] > after]
        lines = lines[-80:]

    return JsonResponse({"lines": lines, "last_id": last_id})


# =========================
# Safe imports
# =========================
def _safe_import_cv2():
    try:
        import cv2  # type: ignore
        return cv2
    except Exception:
        return None


def _safe_import_numpy():
    try:
        import numpy as np  # type: ignore
        return np
    except Exception:
        return None


def _safe_import_face_recognition():
    try:
        import face_recognition  # type: ignore
        return face_recognition
    except Exception:
        return None


def _safe_import_ultralytics():
    try:
        from ultralytics import YOLO  # type: ignore
        return YOLO
    except Exception:
        return None


# =========================
# YOLOv3-tiny (OpenCV DNN)
# =========================
YOLO_CONFIG = {
    "weights": os.path.join(settings.BASE_DIR, "camera", "yolov3-tiny.weights"),
    "cfg": os.path.join(settings.BASE_DIR, "camera", "yolov3-tiny.cfg"),
    "classes": os.path.join(settings.BASE_DIR, "camera", "coco.names"),

    # Clases COCO que el sistema debe monitorear.
    # Nota: "gun" no existe en coco.names, por eso no se incluye aquí.
    "monitored_classes": [
        "knife",
        "scissors",
        "baseball bat",
        "bottle",
        "cell phone",
        "backpack",
        "handbag",
        "suitcase",
        "cat",
        "dog",
        "bird",
    ],
}

# Reglas de clasificación para los objetos monitoreados por YOLO.
# event_type debe coincidir con los choices del modelo SecurityEvent.
OBJECT_RULES = {
    "knife": {
        "event_type": "dangerous_object",
        "message": "Objeto cortopunzante detectado: cuchillo",
        "priority": "Alta",
        "color": (0, 0, 255),
    },
    "scissors": {
        "event_type": "dangerous_object",
        "message": "Objeto cortopunzante detectado: tijeras",
        "priority": "Alta",
        "color": (0, 0, 255),
    },
    "baseball bat": {
        "event_type": "dangerous_object",
        "message": "Objeto contundente detectado",
        "priority": "Alta",
        "color": (0, 0, 255),
    },
    "bottle": {
        "event_type": "dangerous_object",
        "message": "Botella detectada en zona monitoreada",
        "priority": "Media",
        "color": (0, 165, 255),
    },
    "cell phone": {
        "event_type": "unauthorized_access",
        "message": "Objeto no autorizado detectado: celular",
        "priority": "Media",
        "color": (0, 255, 255),
    },
    "backpack": {
        "event_type": "unauthorized_access",
        "message": "Objeto no autorizado detectado: mochila",
        "priority": "Media",
        "color": (0, 255, 255),
    },
    "handbag": {
        "event_type": "unauthorized_access",
        "message": "Objeto no autorizado detectado: bolso",
        "priority": "Media",
        "color": (0, 255, 255),
    },
    "suitcase": {
        "event_type": "unauthorized_access",
        "message": "Objeto no autorizado detectado: maleta",
        "priority": "Media",
        "color": (0, 255, 255),
    },
    "cat": {
        "event_type": "unauthorized_access",
        "message": "Animal detectado en zona monitoreada: gato",
        "priority": "Media",
        "color": (0, 165, 255),
    },
    "dog": {
        "event_type": "unauthorized_access",
        "message": "Animal detectado en zona monitoreada: perro",
        "priority": "Media",
        "color": (0, 165, 255),
    },
    "bird": {
        "event_type": "unauthorized_access",
        "message": "Animal detectado en zona monitoreada: pájaro",
        "priority": "Media",
        "color": (0, 165, 255),
    },
}

_YOLO_CACHE = {"net": None, "classes": None}


def _load_yolo():
    if _YOLO_CACHE["net"] is not None and _YOLO_CACHE["classes"] is not None:
        return _YOLO_CACHE["net"], _YOLO_CACHE["classes"]

    cv2 = _safe_import_cv2()
    if cv2 is None:
        _log_line("OpenCV no disponible: YOLO deshabilitado", key="cv2_missing", throttle_sec=10)
        return None, None

    if not (
        os.path.exists(YOLO_CONFIG["weights"])
        and os.path.exists(YOLO_CONFIG["cfg"])
        and os.path.exists(YOLO_CONFIG["classes"])
    ):
        _log_line("Archivos YOLO no encontrados (weights/cfg/classes)", key="yolo_files_missing", throttle_sec=10)
        return None, None

    try:
        net = cv2.dnn.readNet(YOLO_CONFIG["weights"], YOLO_CONFIG["cfg"])
        with open(YOLO_CONFIG["classes"], "r", encoding="utf-8") as f:
            classes = [line.strip() for line in f.readlines()]

        _YOLO_CACHE["net"] = net
        _YOLO_CACHE["classes"] = classes
        _log_line("✅ YOLO cargado", key="yolo_loaded", throttle_sec=10)
        return net, classes
    except Exception as e:
        _log_line(f"❌ Error cargando YOLO: {e}", key="yolo_load_err", throttle_sec=10)
        return None, None


# =========================
# PPE (Ultralytics)
# =========================
_PPE_CACHE = {"model": None}


def _load_ppe_model():
    if _PPE_CACHE["model"] is not None:
        return _PPE_CACHE["model"]

    YOLO = _safe_import_ultralytics()
    if YOLO is None:
        _log_line("Ultralytics no disponible: PPE deshabilitado", key="ultra_missing", throttle_sec=10)
        return None

    model_path = os.path.join(settings.BASE_DIR, "camera", "ppe.pt")
    if not os.path.exists(model_path):
        _log_line(f"❌ No existe ppe.pt en: {model_path}", key="ppe_file_missing", throttle_sec=10)
        return None

    try:
        model = YOLO(model_path)
        _PPE_CACHE["model"] = model
        _log_line("✅ PPE model cargado", key="ppe_loaded", throttle_sec=10)
        return model
    except Exception as e:
        _log_line(f"❌ Error cargando PPE model: {e}", key="ppe_load_err", throttle_sec=10)
        return None


# =========================
# Frames (con FPS lento)
# =========================
def gen_frames(camera: Camera, target_fps: int = 10):
    cv2 = _safe_import_cv2()
    np = _safe_import_numpy()

    if cv2 is None or np is None:
        _log_line("❌ Falta cv2 o numpy", key="deps_missing", throttle_sec=5)
        return

    net, coco_classes = _load_yolo()
    ppe_model = _load_ppe_model()

    camera_source = camera.get_video_source()
    camera_name = camera.nombre

    if isinstance(camera_source, int):
        cap = cv2.VideoCapture(camera_source, cv2.CAP_DSHOW)
    else:
        cap = cv2.VideoCapture(camera_source)

    if not cap.isOpened():
        _log_line(f"❌ No se pudo abrir la cámara: {camera_name}", key=f"cam_fail_{camera.id}", throttle_sec=10)
        return

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

    face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")

    # Face recognition setup
    face_rec = _safe_import_face_recognition()
    last_face_db_sync = 0.0
    known_face_encodings = []
    known_face_metadata = []
    current_faces = []
    last_detected_faces = []

    frame_counter = 0
    last_ppe_event_frame = -999

    target_fps = max(1, min(int(target_fps), 30))
    frame_interval = 1.0 / float(target_fps)
    next_frame_at = time.monotonic()

    _log_line(
        f"🟢 Streaming iniciado: {camera_name} (fps={target_fps})",
        key=f"stream_start_{camera.id}",
        throttle_sec=2
    )

    try:
        while True:
            now = time.monotonic()

            if now < next_frame_at:
                time.sleep(next_frame_at - now)

            next_frame_at = max(next_frame_at + frame_interval, time.monotonic() + 0.001)

            ok, frame = cap.read()

            if not ok:
                _log_line(
                    f"❌ No se pudo leer frame de {camera_name}",
                    key=f"frame_fail_{camera.id}",
                    throttle_sec=5
                )
                break

            frame_counter += 1
            small_frame = cv2.resize(frame, (320, 240))

            # Face detection and recognition
            if face_rec is not None:
                if frame_counter % 6 == 0:
                    # Sync authorized faces from database periodically (every 10 seconds)
                    if now - last_face_db_sync > 10.0:
                        known_face_encodings = []
                        known_face_metadata = []
                        try:
                            for person in AuthorizedPerson.objects.filter(is_active=True):
                                try:
                                    enc = json.loads(person.face_encoding)
                                    known_face_encodings.append(np.array(enc))
                                    known_face_metadata.append({
                                        "name": person.get_full_name(),
                                        "person": person
                                    })
                                except Exception as e:
                                    print(f"Error parsing encoding for {person}: {e}")
                        except Exception as e:
                            print(f"Error loading authorized persons: {e}")
                        last_face_db_sync = now

                    # Run recognition
                    rgb_small = cv2.cvtColor(small_frame, cv2.COLOR_BGR2RGB)
                    face_locations = face_rec.face_locations(rgb_small)
                    face_encodings = face_rec.face_encodings(rgb_small, face_locations)

                    new_detected_faces = []
                    current_faces = []

                    for face_loc, face_encoding in zip(face_locations, face_encodings):
                        top, right, bottom, left = face_loc
                        cx = (left + right) // 2
                        cy = (top + bottom) // 2

                        x1 = left * 2
                        y1 = top * 2
                        x2 = right * 2
                        y2 = bottom * 2

                        # Try to match with a previously tracked face (Euclidean distance < 50px)
                        tracked_face = None
                        best_dist = 999.0
                        for f in last_detected_faces:
                            dist = np.sqrt((cx - f["center"][0])**2 + (cy - f["center"][1])**2)
                            if dist < 50.0 and dist < best_dist:
                                best_dist = dist
                                tracked_face = f

                        # Perform database recognition
                        name = "Desconocido"
                        is_authorized = False
                        person_obj = None

                        if known_face_encodings:
                            # Using 0.6 tolerance (default, balanced)
                            matches = face_rec.compare_faces(known_face_encodings, face_encoding, tolerance=0.6)
                            if True in matches:
                                face_distances = face_rec.face_distance(known_face_encodings, face_encoding)
                                best_match_idx = np.argmin(face_distances)
                                if matches[best_match_idx]:
                                    is_authorized = True
                                    person_obj = known_face_metadata[best_match_idx]["person"]
                                    name = known_face_metadata[best_match_idx]["name"]

                        # Temporal smoothing / Hysteresis:
                        # If current frame says unauthorized, but we recognized them as authorized recently (last 4 seconds),
                        # preserve their authorized status to avoid flashing.
                        if not is_authorized and tracked_face is not None:
                            if tracked_face["is_authorized"] and (now - tracked_face["last_authorized_ts"] < 4.0):
                                is_authorized = True
                                person_obj = tracked_face["person_obj"]
                                name = tracked_face["name"]

                        # Determine last authorized timestamp
                        last_auth_ts = tracked_face["last_authorized_ts"] if (tracked_face and tracked_face["is_authorized"]) else 0.0
                        if is_authorized:
                            last_auth_ts = now

                        new_detected_faces.append({
                            "center": (cx, cy),
                            "name": name,
                            "is_authorized": is_authorized,
                            "person_obj": person_obj,
                            "last_authorized_ts": last_auth_ts,
                            "coords": (x1, y1, x2, y2),
                            "last_seen": now
                        })

                        if is_authorized:
                            current_faces.append((x1, y1, x2, y2, f"Autorizado: {name}", (0, 255, 0)))

                            # Log and alert
                            _log_line(
                                f"FACE [{camera_name}]: ✅ Autorizado: {name}",
                                key=f"face_rec_log_{camera.id}_{person_obj.id}",
                                throttle_sec=15.0
                            )
                            event_key = f"face_rec_event_{camera.id}_{person_obj.id}"
                            if can_save_event(event_key, seconds=30):
                                try:
                                    create_security_event(
                                        event_type="face_recognized",
                                        details=f"Rostro autorizado detectado: {name} (Cargo: {person_obj.cargo})",
                                        frame=frame.copy(),
                                        camera=camera,
                                        authorized_person=person_obj,
                                        epp_correcto=True,
                                    )
                                except Exception as e:
                                    print(f"Error saving recognized event: {e}")
                        else:
                            current_faces.append((x1, y1, x2, y2, "NO AUTORIZADO", (0, 0, 255)))

                            # Log and alert
                            _log_line(
                                f"FACE [{camera_name}]: ❌ Persona no autorizada detectada",
                                key=f"face_unauth_log_{camera.id}",
                                throttle_sec=15.0
                            )
                            event_key = f"unauthorized_face_event_{camera.id}"
                            if can_save_event(event_key, seconds=30):
                                try:
                                    create_security_event(
                                        event_type="unauthorized_access",
                                        details="Persona no autorizada detectada en el área monitoreada",
                                        frame=frame.copy(),
                                        camera=camera,
                                        epp_correcto=False,
                                    )
                                except Exception as e:
                                    print(f"Error saving unauthorized event: {e}")

                    # Mantener rostros detectados recientemente para que PPE pueda asociarlos
                    # aunque face_recognition no los vea en este frame exacto.
                    last_detected_faces = _merge_recent_faces(
                        old_faces=last_detected_faces,
                        new_faces=new_detected_faces,
                        now=now,
                    )
            else:
                # Fallback to Haar Cascade
                if frame_counter % 3 == 0:
                    gray = cv2.cvtColor(small_frame, cv2.COLOR_BGR2GRAY)
                    faces = face_cascade.detectMultiScale(gray, 1.1, 4)

                    if len(faces) > 0:
                        _log_line(
                            f"FACE [{camera_name}]: {len(faces)} rostro(s)",
                            key=f"face_count_{camera.id}",
                            throttle_sec=0.8
                        )

                    current_faces = []
                    for (x, y, w, h) in faces:
                        x1 = x * 2
                        y1 = y * 2
                        x2 = (x + w) * 2
                        y2 = (y + h) * 2
                        current_faces.append((x1, y1, x2, y2, "Rostro", (255, 0, 0)))

            # Draw current faces bounding boxes and labels
            for x1, y1, x2, y2, label, color in current_faces:
                cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
                cv2.putText(
                    frame,
                    label,
                    (x1, max(y1 - 10, 20)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.6,
                    color,
                    2,
                )

            # YOLO dangerous objects
            if frame_counter % 6 == 0 and net is not None and coco_classes is not None:
                blob = cv2.dnn.blobFromImage(
                    frame,
                    1 / 255.0,
                    (416, 416),
                    swapRB=True,
                    crop=False
                )

                net.setInput(blob)
                outs = net.forward(net.getUnconnectedOutLayersNames())

                height, width = frame.shape[:2]
                boxes, confs, class_ids = [], [], []

                for out in outs:
                    for det in out:
                        scores = det[5:]
                        class_id = int(np.argmax(scores))
                        confidence = float(scores[class_id])

                        if confidence > 0.35:
                            label = coco_classes[class_id]

                            if label not in YOLO_CONFIG["monitored_classes"]:
                                continue

                            min_confidence = (
                                ANIMAL_OBJECT_CONFIDENCE
                                if label in ANIMAL_CLASSES
                                else DEFAULT_OBJECT_CONFIDENCE
                            )

                            if confidence < min_confidence:
                                continue

                            cx = int(det[0] * width)
                            cy = int(det[1] * height)
                            w = int(det[2] * width)
                            h = int(det[3] * height)
                            x = int(cx - w / 2)
                            y = int(cy - h / 2)

                            boxes.append([x, y, w, h])
                            confs.append(confidence)
                            class_ids.append(class_id)

                if boxes:
                    idxs = cv2.dnn.NMSBoxes(boxes, confs, 0.20, 0.4)
                    idxs = idxs.flatten().tolist() if hasattr(idxs, "flatten") else list(idxs)

                    for i in idxs:
                        x, y, w, h = boxes[i]
                        label = coco_classes[class_ids[i]]
                        conf = confs[i]

                        rule = OBJECT_RULES.get(label)

                        if rule is None:
                            continue

                        event_type = rule["event_type"]
                        priority = rule["priority"]
                        message = rule["message"]
                        color = rule["color"]

                        _log_line(
                            f"OBJ [{camera_name}]: {label} ({conf:.2f}) | {priority}",
                            key=f"obj_{camera.id}_{label}",
                            throttle_sec=0.25,
                        )

                        cv2.rectangle(frame, (x, y), (x + w, y + h), color, 2)

                        cv2.putText(
                            frame,
                            f"{label}: {conf:.2f} | {priority}",
                            (x, max(y - 10, 20)),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.6,
                            color,
                            2,
                        )

                        event_key = f"{event_type}_camera_{camera.id}_{label}"

                        if can_save_event(event_key, seconds=20):
                            try:
                                create_security_event(
                                    event_type=event_type,
                                    details=f"{message} | Prioridad: {priority} | Confianza: {conf:.2f}",
                                    frame=frame.copy(),
                                    user=None,
                                    camera=camera,
                                    epp_correcto=False,
                                )

                                _log_line(
                                    f"📸 Evidencia guardada [{camera_name}]: {label} ({priority})",
                                    key=f"evidence_{camera.id}_{event_type}_{label}",
                                    throttle_sec=2,
                                )

                            except Exception as e:
                                _log_line(
                                    f"❌ Error guardando {event_type}: {e}",
                                    key=f"db_{camera.id}_{event_type}_err",
                                    throttle_sec=5,
                                )

            # PPE
            if ppe_model is not None and frame_counter % 15 == 0:
                try:
                    res = ppe_model(frame, verbose=False)[0]
                    boxes = res.boxes
                    names = res.names

                    persons = []
                    items = []

                    for b in boxes:
                        cls_id = int(b.cls[0])
                        label = str(names.get(cls_id, cls_id)).lower()
                        conf = float(b.conf[0])
                        x1, y1, x2, y2 = map(int, b.xyxy[0].tolist())

                        if conf < 0.55:
                            continue

                        if label == "person":
                            persons.append((x1, y1, x2, y2))
                        else:
                            items.append((label, conf, x1, y1, x2, y2))

                    for (px1, py1, px2, py2) in persons:
                        identity = _match_identity_to_person_box(
                            (px1, py1, px2, py2),
                            last_detected_faces
                        )

                        # Si no pudo asociar por la caja del rostro,
                        # pero solo hay una persona detectada por PPE,
                        # usa el último rostro reconocido recientemente.
                        if identity is None and len(persons) == 1:
                            identity = _get_recent_identity(last_detected_faces, now)

                        person_name, auth_status, authorized_person = _get_identity_text(identity)

                        present = set()
                        negatives = set()

                        for (label, conf, x1, y1, x2, y2) in items:
                            cx = (x1 + x2) // 2
                            cy = (y1 + y2) // 2

                            if px1 <= cx <= px2 and py1 <= cy <= py2:
                                present.add(label)

                                if label.startswith("no-"):
                                    negatives.add(label)

                        if negatives:
                            base_msg = "⚠ Indumentaria incorrecta: " + ", ".join(sorted([x.upper() for x in negatives]))
                            msg = f"{base_msg} | Persona: {person_name} | Estado: {auth_status}"

                            _log_line(
                                f"PPE [{camera_name}]: {msg}",
                                key=f"ppe_neg_{camera.id}",
                                throttle_sec=0.4
                            )

                            event_key = f"ppe_incorrect_{camera.id}_{_safe_event_key(person_name)}_{_safe_event_key(base_msg)}"

                            if can_save_event(event_key, seconds=25):
                                create_security_event(
                                    event_type="unauthorized_access",
                                    details=msg,
                                    frame=frame.copy(),
                                    camera=camera,
                                    authorized_person=authorized_person,
                                    epp_correcto=False,
                                )

                            cv2.rectangle(frame, (px1, py1), (px2, py2), (0, 255, 255), 2)

                            cv2.putText(
                                frame,
                                msg,
                                (px1, max(py1 - 10, 20)),
                                cv2.FONT_HERSHEY_SIMPLEX,
                                0.7,
                                (0, 255, 255),
                                2
                            )

                            continue

                        missing = []

                        if "hardhat" not in present:
                            missing.append("hardhat")

                        if "safety vest" not in present:
                            missing.append("safety vest")

                        if "mask" not in present:
                            missing.append("mask")

                        if missing:
                            base_msg = f"⚠ Falta EPP: {', '.join(missing)}"
                            msg = f"{base_msg} | Persona: {person_name} | Estado: {auth_status}"

                            _log_line(
                                f"PPE [{camera_name}]: {msg}",
                                key=f"ppe_missing_{camera.id}",
                                throttle_sec=0.4
                            )

                            event_key = f"ppe_missing_{camera.id}_{_safe_event_key(person_name)}_{_safe_event_key(base_msg)}"

                            if can_save_event(event_key, seconds=25):
                                create_security_event(
                                    event_type="unauthorized_access",
                                    details=msg,
                                    frame=frame.copy(),
                                    camera=camera,
                                    authorized_person=authorized_person,
                                    epp_correcto=False,
                                )

                            cv2.rectangle(frame, (px1, py1), (px2, py2), (0, 255, 255), 2)

                            cv2.putText(
                                frame,
                                msg,
                                (px1, max(py1 - 10, 20)),
                                cv2.FONT_HERSHEY_SIMPLEX,
                                0.7,
                                (0, 255, 255),
                                2
                            )

                        else:
                            _log_line(
                                f"PPE [{camera_name}]: ✅ EPP OK",
                                key=f"ppe_ok_{camera.id}",
                                throttle_sec=1.2
                            )

                            cv2.rectangle(frame, (px1, py1), (px2, py2), (0, 255, 0), 2)

                            cv2.putText(
                                frame,
                                "EPP OK",
                                (px1, max(py1 - 10, 20)),
                                cv2.FONT_HERSHEY_SIMPLEX,
                                0.7,
                                (0, 255, 0),
                                2
                            )

                except Exception as e:
                    _log_line(
                        f"❌ Error PPE detect [{camera_name}]: {e}",
                        key=f"ppe_detect_err_{camera.id}",
                        throttle_sec=5
                    )

            ret, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 70])

            if not ret:
                continue

            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n" + buffer.tobytes() + b"\r\n"
            )

    finally:
        cap.release()

        _log_line(
            f"🟠 Streaming detenido: {camera_name}",
            key=f"stream_stop_{camera.id}",
            throttle_sec=2
        )

def _get_request_fps(request):
    try:
        return int(request.GET.get("fps", "5"))
    except ValueError:
        return 5

@gzip.gzip_page
def video_feed(request, camera_id):
    cv2 = _safe_import_cv2()

    if cv2 is None:
        return JsonResponse({"success": False, "message": "OpenCV no está instalado."}, status=400)

    camera = get_object_or_404(Camera, id=camera_id, is_active=True)
    fps = _get_request_fps(request)

    return StreamingHttpResponse(
        gen_frames(camera=camera, target_fps=fps),
        content_type="multipart/x-mixed-replace;boundary=frame",
    )

@gzip.gzip_page
def video_feed_default(request):
    cv2 = _safe_import_cv2()

    if cv2 is None:
        return JsonResponse(
            {"success": False, "message": "OpenCV no está instalado."},
            status=400
        )

    camera = Camera.objects.filter(is_active=True).order_by("id").first()

    if camera is None:
        return JsonResponse(
            {"success": False, "message": "No hay cámaras activas configuradas."},
            status=404
        )

    fps = _get_request_fps(request)

    return StreamingHttpResponse(
        gen_frames(camera=camera, target_fps=fps),
        content_type="multipart/x-mixed-replace;boundary=frame",
    )


@csrf_exempt
def register_face(request):
    if request.method != "POST":
        return JsonResponse(
            {"success": False, "message": "Método no permitido."},
            status=405
        )

    try:
        face_recognition = _safe_import_face_recognition()

        if face_recognition is None:
            return JsonResponse(
                {
                    "success": False,
                    "message": "La librería face_recognition no está instalada."
                },
                status=400
            )

        if not request.user.is_authenticated:
            return JsonResponse(
                {
                    "success": False,
                    "message": "Debes iniciar sesión para registrar un rostro."
                },
                status=401
            )

        nombres = request.POST.get("nombres", "").strip()
        apellidos = request.POST.get("apellidos", "").strip()
        celular = request.POST.get("celular", "").strip()
        correo = request.POST.get("correo", "").strip().lower()
        cargo = request.POST.get("cargo", "").strip()

        image_file = request.FILES.get("image")

        if not nombres:
            return JsonResponse(
                {"success": False, "message": "Ingresa los nombres de la persona."},
                status=400
            )

        if not apellidos:
            return JsonResponse(
                {"success": False, "message": "Ingresa los apellidos de la persona."},
                status=400
            )

        if not correo:
            return JsonResponse(
                {"success": False, "message": "Ingresa el correo de la persona."},
                status=400
            )

        if not cargo:
            return JsonResponse(
                {"success": False, "message": "Ingresa el cargo de la persona."},
                status=400
            )

        if not image_file:
            return JsonResponse(
                {"success": False, "message": "Selecciona una imagen del rostro."},
                status=400
            )

        image_data = face_recognition.load_image_file(image_file)

        face_locations = face_recognition.face_locations(image_data)

        if not face_locations:
            return JsonResponse(
                {
                    "success": False,
                    "message": "No se detectó ningún rostro en la imagen."
                },
                status=400
            )

        if len(face_locations) > 1:
            return JsonResponse(
                {
                    "success": False,
                    "message": "La imagen debe contener solo un rostro."
                },
                status=400
            )

        encodings = face_recognition.face_encodings(image_data, face_locations)

        if not encodings:
            return JsonResponse(
                {
                    "success": False,
                    "message": "No se pudo generar la codificación facial."
                },
                status=400
            )

        encoding_json = json.dumps(encodings[0].tolist())

        image_file.seek(0)
        face_image_path = save_authorized_face_image(image_file, correo)

        person, created = AuthorizedPerson.objects.update_or_create(
            correo=correo,
            defaults={
                "nombres": nombres,
                "apellidos": apellidos,
                "celular": celular,
                "cargo": cargo,
                "face_encoding": encoding_json,
                "face_image_path": face_image_path,
                "registered_by": request.user,
                "is_active": True,
            }
        )

        action = "registrado" if created else "actualizado"

        _log_line(
            f"✅ Rostro autorizado {action}: {person.get_full_name()}",
            key=f"face_registered_{person.id}",
            throttle_sec=1.5
        )

        return JsonResponse(
            {
                "success": True,
                "message": f"Rostro {action} correctamente para {person.get_full_name()}."
            }
        )

    except Exception as e:
        print("[ERROR] register_face:")
        print(traceback.format_exc())

        return JsonResponse(
            {
                "success": False,
                "message": f"Error interno al registrar el rostro: {str(e)}"
            },
            status=500
        )

def get_events(request):
    events = SecurityEvent.objects.order_by("-timestamp")[:50]
    data = [
        {
            "id": event.id,
            "event_type": event.event_type,
            "event_type_display": event.get_event_type_display(),
            "details": event.details,
            "timestamp": event.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            "resolved": event.resolved,
            "image_path": event.get_image_url() if hasattr(event, "get_image_url") else None,
        }
        for event in events
    ]
    return JsonResponse({"events": data})


@csrf_exempt
def mark_event_resolved(request, event_id):
    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "Método no permitido"}, status=405)

    event = get_object_or_404(SecurityEvent, id=event_id)
    event.resolved = True
    event.save()
    _log_line(f"✅ Evento resuelto: {event_id}", key=f"ev_res_{event_id}", throttle_sec=0.5)
    return JsonResponse({"status": "success"})


def get_security_events(request):
    events = SecurityEvent.objects.all().order_by("-timestamp")[:50]
    events_data = []
    for event in events:
        events_data.append(
            {
                "id": event.id,
                "event_type": event.event_type,
                "event_type_display": event.get_event_type_display(),
                "details": event.details,
                "timestamp": event.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                "resolved": event.resolved,
                "image_url": event.get_image_url() if hasattr(event, "get_image_url") else None,
                "camera": event.camera.nombre if event.camera else "Sin cámara",
                "user": event.related_user.username if getattr(event, "related_user", None) else "Sistema",
            }
        )
    return JsonResponse({"events": events_data})


@csrf_exempt
def mark_event_as_resolved(request, event_id):
    return mark_event_resolved(request, event_id)


class CameraView(TemplateView):
    template_name = "camera/camera.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        cameras = Camera.objects.filter(is_active=True).order_by("id")

        context["cameras"] = cameras
        context["selected_camera"] = cameras.first()

        return context


class AlertaView(TemplateView):
    template_name = "alertas/alerta.html"
