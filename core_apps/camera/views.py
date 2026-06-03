from __future__ import annotations

import json
import os
import time
import traceback
from collections import deque
from threading import Lock

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404
from django.views.decorators import gzip
from django.views.decorators.http import require_GET, require_POST
from django.views.generic import TemplateView

from .models import AuthorizedPerson, Camera, SecurityEvent
from core_apps.camera.object_rules import OBJECT_RULES
from core_apps.camera.utils import (
    can_save_event,
    create_security_event,
    save_authorized_face_image,
)

# =========================
# LIVE LOG (RAM) - incremental
# =========================
_LIVE_LOG = deque(maxlen=300)
_LOG_LOCK = Lock()
_LOG_SEQ = 0
_LAST_LOG_TS: dict[str, float] = {}


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


@require_GET
@login_required
def live_status(request):
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
    "monitored_classes": list(OBJECT_RULES.keys()),
}

_YOLO_CACHE = {"net": None, "classes": None}
_PPE_CACHE = {"model": None}


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
# Helpers de cámara
# =========================
def _get_request_fps(request):
    try:
        return int(request.GET.get("fps", "8"))
    except ValueError:
        return 8


def _open_camera_source(camera: Camera):
    cv2 = _safe_import_cv2()
    if cv2 is None:
        return None, "OpenCV no está instalado."

    source = camera.get_video_source()

    try:
        if source is None:
            return None, f"Fuente de video vacía para la cámara: {camera.nombre}"

        if isinstance(source, int):
            cap = cv2.VideoCapture(source, cv2.CAP_DSHOW)
        else:
            cap = cv2.VideoCapture(source)

        if not cap or not cap.isOpened():
            if cap:
                cap.release()
            return None, f"No se pudo abrir la cámara: {camera.nombre}"

        return cap, None
    except Exception as e:
        return None, f"Error al intentar abrir la cámara {camera.nombre}: {str(e)}"


@require_GET
@login_required
def camera_status(request):
    camera = Camera.objects.filter(is_active=True).order_by("id").first()

    if camera is None:
        return JsonResponse(
            {"success": False, "message": "No hay cámaras activas configuradas."},
            status=404,
        )

    cap, error = _open_camera_source(camera)
    if error:
        _log_line(f"❌ {error}", key=f"cam_status_fail_{camera.id}", throttle_sec=5)
        return JsonResponse({"success": False, "message": error}, status=400)

    cap.release()
    return JsonResponse(
        {"success": True, "message": f"Cámara disponible: {camera.nombre}"},
        status=200,
    )


# =========================
# Frames
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

    if camera_source is None:
        _log_line(
            f"❌ Fuente de video vacía para la cámara: {camera_name}",
            key=f"empty_source_{camera.id}",
            throttle_sec=10
        )
        return

    if isinstance(camera_source, int):
        cap = cv2.VideoCapture(camera_source, cv2.CAP_DSHOW)
    else:
        cap = cv2.VideoCapture(camera_source)

    if not cap.isOpened():
        _log_line(f"❌ No se pudo abrir la cámara: {camera_name}", key=f"cam_fail_{camera.id}", throttle_sec=10)
        return

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

    face_cascade = cv2.CascadeClassifier(
        cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    )

    frame_counter = 0
    last_ppe_event_frame = 0

    target_fps = max(1, min(int(target_fps), 30))
    frame_interval = 1.0 / float(target_fps)
    next_frame_at = time.monotonic()

    _log_line(
        f"🟢 Streaming iniciado: {camera_name} (fps={target_fps})",
        key=f"stream_start_{camera.id}",
        throttle_sec=2,
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
                    throttle_sec=5,
                )
                break

            frame_counter += 1
            small_frame = cv2.resize(frame, (320, 240))

            # Haar faces
            if frame_counter % 3 == 0:
                gray = cv2.cvtColor(small_frame, cv2.COLOR_BGR2GRAY)
                faces = face_cascade.detectMultiScale(gray, 1.1, 4)

                if len(faces) > 0:
                    _log_line(
                        f"FACE [{camera_name}]: {len(faces)} rostro(s)",
                        key=f"face_count_{camera.id}",
                        throttle_sec=0.8,
                    )

                for (x, y, w, h) in faces:
                    x, y, w, h = x * 2, y * 2, w * 2, h * 2
                    cv2.rectangle(frame, (x, y), (x + w, y + h), (255, 0, 0), 2)

            # YOLO monitored objects
            if frame_counter % 6 == 0 and net is not None and coco_classes is not None:
                blob = cv2.dnn.blobFromImage(
                    small_frame,
                    1 / 255.0,
                    (320, 320),
                    swapRB=True,
                    crop=False,
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

                        if confidence > 0.5:
                            label = coco_classes[class_id]

                            if label in YOLO_CONFIG["monitored_classes"]:
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
                    idxs = cv2.dnn.NMSBoxes(boxes, confs, 0.5, 0.4)
                    idxs = idxs.flatten().tolist() if hasattr(idxs, "flatten") else list(idxs)

                    for i in idxs:
                        x, y, w, h = boxes[i]
                        label = coco_classes[class_ids[i]]
                        conf = confs[i]

                        rule = OBJECT_RULES.get(label)
                        if rule is None:
                            continue

                        event_type = rule["event_type"]
                        category = rule["category"]
                        severity = rule["severity"]
                        should_alert = rule["should_alert"]
                        message = rule["message"]
                        color = rule["color"]

                        severity_label = {
                            "critical": "CRÍTICO",
                            "high": "ALTO",
                            "medium": "MEDIO",
                            "low": "BAJO",
                            "info": "INFO",
                        }.get(severity, severity.upper())

                        _log_line(
                            f"OBJ [{camera_name}]: {label} ({conf:.2f}) | {severity_label}",
                            key=f"obj_{camera.id}_{label}",
                            throttle_sec=0.25,
                        )

                        cv2.rectangle(frame, (x, y), (x + w, y + h), color, 2)
                        cv2.putText(
                            frame,
                            f"{label}: {conf:.2f} | {severity_label}",
                            (x, max(y - 10, 20)),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.6,
                            color,
                            2,
                        )

                        event_key = f"{event_type}_camera_{camera.id}_{label}_{severity}"

                        if can_save_event(event_key, seconds=20):
                            try:
                                create_security_event(
                                    event_type=event_type,
                                    details=f"{message} | Severidad: {severity_label} | Confianza: {conf:.2f}",
                                    frame=frame.copy(),
                                    user=None,
                                    camera=camera,
                                    category=category,
                                    severity=severity,
                                    object_label=label,
                                    should_alert=should_alert,
                                )

                                _log_line(
                                    f"📸 Evidencia guardada [{camera_name}]: {label} ({severity_label})",
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
            if ppe_model is not None and frame_counter % 10 == 0:
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
                            msg = "⚠ Indumentaria incorrecta: " + ", ".join(
                                sorted([x.upper() for x in negatives])
                            )

                            _log_line(
                                f"PPE [{camera_name}]: {msg}",
                                key=f"ppe_neg_{camera.id}",
                                throttle_sec=0.4,
                            )

                            if (frame_counter - last_ppe_event_frame) > 60:
                                create_security_event(
                                    event_type="ppe_incorrect",
                                    details=msg,
                                    frame=frame.copy(),
                                    camera=camera,
                                    epp_correcto=False,
                                    category="ppe",
                                    severity="high",
                                    object_label=",".join(sorted(negatives)),
                                    should_alert=True,
                                )
                                last_ppe_event_frame = frame_counter

                            cv2.rectangle(frame, (px1, py1), (px2, py2), (0, 255, 255), 2)
                            cv2.putText(
                                frame,
                                msg,
                                (px1, max(py1 - 10, 20)),
                                cv2.FONT_HERSHEY_SIMPLEX,
                                0.7,
                                (0, 255, 255),
                                2,
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
                            msg = f"⚠ Falta EPP: {', '.join(missing)}"

                            _log_line(
                                f"PPE [{camera_name}]: {msg}",
                                key=f"ppe_missing_{camera.id}",
                                throttle_sec=0.4,
                            )

                            if (frame_counter - last_ppe_event_frame) > 60:
                                create_security_event(
                                    event_type="ppe_missing",
                                    details=msg,
                                    frame=frame.copy(),
                                    camera=camera,
                                    epp_correcto=False,
                                    category="ppe",
                                    severity="high",
                                    object_label=",".join(missing),
                                    should_alert=True,
                                )
                                last_ppe_event_frame = frame_counter

                            cv2.rectangle(frame, (px1, py1), (px2, py2), (0, 255, 255), 2)
                            cv2.putText(
                                frame,
                                msg,
                                (px1, max(py1 - 10, 20)),
                                cv2.FONT_HERSHEY_SIMPLEX,
                                0.7,
                                (0, 255, 255),
                                2,
                            )

                        else:
                            _log_line(
                                f"PPE [{camera_name}]: ✅ EPP OK",
                                key=f"ppe_ok_{camera.id}",
                                throttle_sec=1.2,
                            )

                            cv2.rectangle(frame, (px1, py1), (px2, py2), (0, 255, 0), 2)
                            cv2.putText(
                                frame,
                                "EPP OK",
                                (px1, max(py1 - 10, 20)),
                                cv2.FONT_HERSHEY_SIMPLEX,
                                0.7,
                                (0, 255, 0),
                                2,
                            )

                except Exception as e:
                    _log_line(
                        f"❌ Error PPE detect [{camera_name}]: {e}",
                        key=f"ppe_detect_err_{camera.id}",
                        throttle_sec=5,
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
            throttle_sec=2,
        )


# =========================
# Video feed por cámara
# =========================
@require_GET
@login_required
@gzip.gzip_page
def video_feed(request, camera_id):
    cv2 = _safe_import_cv2()

    if cv2 is None:
        return JsonResponse(
            {"success": False, "message": "OpenCV no está instalado."},
            status=400
        )

    camera = get_object_or_404(Camera, id=camera_id, is_active=True)
    fps = _get_request_fps(request)

    return StreamingHttpResponse(
        gen_frames(camera=camera, target_fps=fps),
        content_type="multipart/x-mixed-replace;boundary=frame",
    )


@require_GET
@login_required
@gzip.gzip_page
def video_feed_camera(request, camera_id):
    camera = get_object_or_404(Camera, id=camera_id, is_active=True)

    _log_line(
        f"🎥 Cámara seleccionada: {camera.nombre}",
        key=f"camera_selected_{camera.id}",
        throttle_sec=2,
    )

    return video_feed(request, camera_id)


@require_GET
@login_required
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

    return video_feed(request, camera.id)


# =========================
# Registro facial
# =========================
@require_POST
@login_required
def register_face(request):
    try:
        face_recognition = _safe_import_face_recognition()

        if face_recognition is None:
            return JsonResponse(
                {"success": False, "message": "La librería face_recognition no está instalada."},
                status=400
            )

        nombres = request.POST.get("nombres", "").strip()
        apellidos = request.POST.get("apellidos", "").strip()
        celular = request.POST.get("celular", "").strip()
        correo = request.POST.get("correo", "").strip().lower()
        cargo = request.POST.get("cargo", "").strip()
        image_file = request.FILES.get("image")

        if not nombres:
            return JsonResponse({"success": False, "message": "Ingresa los nombres de la persona."}, status=400)
        if not apellidos:
            return JsonResponse({"success": False, "message": "Ingresa los apellidos de la persona."}, status=400)
        if not correo:
            return JsonResponse({"success": False, "message": "Ingresa el correo de la persona."}, status=400)
        if not cargo:
            return JsonResponse({"success": False, "message": "Ingresa el cargo de la persona."}, status=400)
        if not image_file:
            return JsonResponse({"success": False, "message": "Selecciona una imagen del rostro."}, status=400)

        image_data = face_recognition.load_image_file(image_file)
        face_locations = face_recognition.face_locations(image_data)

        if not face_locations:
            return JsonResponse(
                {"success": False, "message": "No se detectó ningún rostro en la imagen."},
                status=400
            )

        if len(face_locations) > 1:
            return JsonResponse(
                {"success": False, "message": "La imagen debe contener solo un rostro."},
                status=400
            )

        encodings = face_recognition.face_encodings(image_data, face_locations)

        if not encodings:
            return JsonResponse(
                {"success": False, "message": "No se pudo generar la codificación facial."},
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
            {"success": False, "message": f"Error interno al registrar el rostro: {str(e)}"},
            status=500
        )


# =========================
# Eventos
# =========================
@require_GET
@login_required
def get_events(request):
    events = SecurityEvent.objects.select_related("camera", "related_user").order_by("-timestamp")[:50]
    data = [
        {
            "id": event.id,
            "event_type": event.event_type,
            "event_type_display": event.get_event_type_display(),
            "category": event.category,
            "severity": event.severity,
            "object_label": event.object_label,
            "should_alert": event.should_alert,
            "details": event.details,
            "timestamp": event.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            "resolved": event.resolved,
            "image_url": event.get_image_url() if hasattr(event, "get_image_url") else None,
            "camera": event.camera.nombre if getattr(event, "camera", None) else "Sin cámara",
        }
        for event in events
    ]
    return JsonResponse({"events": data})


@require_GET
@login_required
def get_security_events(request):
    events = SecurityEvent.objects.select_related("camera", "related_user").order_by("-timestamp")[:50]
    events_data = []
    for event in events:
        events_data.append(
            {
                "id": event.id,
                "event_type": event.event_type,
                "event_type_display": event.get_event_type_display(),
                "category": event.category,
                "severity": event.severity,
                "object_label": event.object_label,
                "should_alert": event.should_alert,
                "details": event.details,
                "timestamp": event.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                "resolved": event.resolved,
                "image_url": event.get_image_url() if hasattr(event, "get_image_url") else None,
                "camera": event.camera.nombre if event.camera else "Sin cámara",
                "user": event.related_user.username if getattr(event, "related_user", None) else "Sistema",
            }
        )
    return JsonResponse({"events": events_data})


@require_POST
@login_required
def mark_event_resolved(request, event_id):
    event = get_object_or_404(SecurityEvent, id=event_id)
    event.resolved = True
    event.save()

    _log_line(f"✅ Evento resuelto: {event_id}", key=f"ev_res_{event_id}", throttle_sec=0.5)
    return JsonResponse({"status": "success"})


# =========================
# Views
# =========================
class CameraView(LoginRequiredMixin, TemplateView):
    template_name = "camera/camera.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        cameras = Camera.objects.filter(is_active=True).order_by("id")
        context["cameras"] = cameras
        context["selected_camera"] = cameras.first()
        return context


class AlertaView(LoginRequiredMixin, TemplateView):
    template_name = "alertas/alerta.html"
