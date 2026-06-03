from __future__ import annotations

import time

from ..utils import create_security_event
from .analysis_config import ANALYSIS_CONFIG
from .event_guard import build_event_key, should_emit_event
from .live_log import log_line
from .model_loader import (
    YOLO_CONFIG,
    load_ppe_model,
    load_yolo,
    safe_import_cv2,
    safe_import_numpy,
)


def _safe_nms_indexes(cv2, boxes, confs):
    idxs = cv2.dnn.NMSBoxes(
        boxes,
        confs,
        ANALYSIS_CONFIG["danger_conf_threshold"],
        0.4,
    )
    if idxs is None:
        return []
    if hasattr(idxs, "flatten"):
        return idxs.flatten().tolist()
    return list(idxs)


def gen_frames(stream, target_fps: int | None = None):
    cv2 = safe_import_cv2()
    np = safe_import_numpy()
    net, coco_classes = load_yolo()
    ppe_model = load_ppe_model()

    if cv2 is None or np is None:
        log_line("❌ Falta cv2 o numpy", key="deps_missing", throttle_sec=5)
        if stream:
            stream.stop()
        return

    target_fps = target_fps or ANALYSIS_CONFIG["stream_fps"]
    target_fps = max(1, min(int(target_fps), 15))
    frame_interval = 1.0 / float(target_fps)

    analysis_width = ANALYSIS_CONFIG["analysis_width"]
    analysis_height = ANALYSIS_CONFIG["analysis_height"]
    output_width = ANALYSIS_CONFIG["output_width"]
    output_height = ANALYSIS_CONFIG["output_height"]
    jpeg_quality = ANALYSIS_CONFIG["jpeg_quality"]

    face_interval = ANALYSIS_CONFIG["face_interval"]
    danger_interval = ANALYSIS_CONFIG["danger_interval"]
    ppe_interval = ANALYSIS_CONFIG["ppe_interval"]

    danger_event_cooldown = ANALYSIS_CONFIG["danger_event_cooldown"]
    ppe_event_cooldown = ANALYSIS_CONFIG["ppe_event_cooldown"]

    danger_conf_threshold = ANALYSIS_CONFIG["danger_conf_threshold"]
    ppe_conf_threshold = ANALYSIS_CONFIG["ppe_conf_threshold"]

    required_ppe_labels = set(label.lower() for label in ANALYSIS_CONFIG["required_ppe_labels"])
    negative_ppe_prefix = ANALYSIS_CONFIG["negative_ppe_prefix"]
    overlay_text = ANALYSIS_CONFIG["overlay_text"]
    dangerous_classes = set(ANALYSIS_CONFIG["dangerous_classes"])

    face_cascade = cv2.CascadeClassifier(
        cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    )

    last_face_run = 0.0
    last_danger_run = 0.0
    last_ppe_run = 0.0

    cached_faces = []
    cached_danger_items = []
    cached_ppe_overlays = []

    log_line(f"🟢 Streaming iniciado (fps={target_fps})", key="stream_start", throttle_sec=2)

    try:
        while True:
            loop_started = time.monotonic()

            ok, frame = stream.read()
            if not ok or frame is None:
                log_line("❌ No se pudo leer frame", key="frame_fail", throttle_sec=5)
                time.sleep(0.01)
                continue

            frame_h, frame_w = frame.shape[:2]
            display_frame = frame.copy()

            small_frame = cv2.resize(
                frame,
                (analysis_width, analysis_height),
                interpolation=cv2.INTER_AREA,
            )

            scale_x = frame_w / float(analysis_width)
            scale_y = frame_h / float(analysis_height)

            now = time.monotonic()

            # Rostros
            if (now - last_face_run) >= face_interval:
                gray = cv2.cvtColor(small_frame, cv2.COLOR_BGR2GRAY)
                faces = face_cascade.detectMultiScale(gray, 1.1, 4)

                cached_faces = []
                for (x, y, w, h) in faces:
                    cached_faces.append(
                        (
                            int(x * scale_x),
                            int(y * scale_y),
                            int(w * scale_x),
                            int(h * scale_y),
                        )
                    )

                if len(cached_faces) > 0:
                    log_line(
                        f"FACE: {len(cached_faces)} rostro(s)",
                        key="face_count",
                        throttle_sec=1.2,
                    )

                last_face_run = now

            # Objetos peligrosos
            if (now - last_danger_run) >= danger_interval and net is not None and coco_classes is not None:
                blob = cv2.dnn.blobFromImage(
                    small_frame, 1 / 255.0, (256, 256), swapRB=True, crop=False
                )
                net.setInput(blob)
                outs = net.forward(net.getUnconnectedOutLayersNames())

                boxes = []
                confs = []
                class_ids = []

                for out in outs:
                    for det in out:
                        scores = det[5:]
                        class_id = int(np.argmax(scores))
                        confidence = float(scores[class_id])

                        if confidence <= danger_conf_threshold:
                            continue

                        label = coco_classes[class_id]
                        if label not in dangerous_classes:
                            continue

                        cx = int(det[0] * analysis_width)
                        cy = int(det[1] * analysis_height)
                        w = int(det[2] * analysis_width)
                        h = int(det[3] * analysis_height)
                        x = int(cx - w / 2)
                        y = int(cy - h / 2)

                        boxes.append([x, y, w, h])
                        confs.append(confidence)
                        class_ids.append(class_id)

                cached_danger_items = []

                if boxes:
                    for i in _safe_nms_indexes(cv2, boxes, confs):
                        x, y, w, h = boxes[i]
                        label = coco_classes[class_ids[i]]
                        conf = confs[i]

                        sx = int(x * scale_x)
                        sy = int(y * scale_y)
                        sw = int(w * scale_x)
                        sh = int(h * scale_y)

                        cached_danger_items.append(
                            {
                                "label": label,
                                "conf": conf,
                                "box": (sx, sy, sw, sh),
                            }
                        )

                        log_line(
                            f"OBJ: {label} ({conf:.2f})",
                            key=f"obj_{label}",
                            throttle_sec=1.0,
                        )

                        event_details = f"Se detectó objeto peligroso: {label} (confianza {conf:.2f})"
                        event_key = build_event_key(
                            event_type="dangerous_object",
                            details=f"{label}",
                            zone="camera_main",
                        )

                        if should_emit_event(event_key, danger_event_cooldown):
                            try:
                                create_security_event(
                                    event_type="dangerous_object",
                                    details=event_details,
                                    frame=frame,
                                )
                            except Exception as e:
                                log_line(
                                    f"❌ Error guardando dangerous_object: {e}",
                                    key="db_danger_err",
                                    throttle_sec=5,
                                )

                last_danger_run = now

            # EPP
            if (now - last_ppe_run) >= ppe_interval and ppe_model is not None:
                try:
                    res = ppe_model(small_frame, verbose=False)[0]
                    boxes = res.boxes
                    names = res.names

                    persons = []
                    items = []

                    for b in boxes:
                        cls_id = int(b.cls[0])
                        label = str(names.get(cls_id, cls_id)).lower()
                        conf = float(b.conf[0])
                        x1, y1, x2, y2 = map(int, b.xyxy[0].tolist())

                        if conf < ppe_conf_threshold:
                            continue

                        if label == "person":
                            persons.append(
                                (
                                    int(x1 * scale_x),
                                    int(y1 * scale_y),
                                    int(x2 * scale_x),
                                    int(y2 * scale_y),
                                )
                            )
                        else:
                            items.append(
                                (
                                    label,
                                    conf,
                                    int(x1 * scale_x),
                                    int(y1 * scale_y),
                                    int(x2 * scale_x),
                                    int(y2 * scale_y),
                                )
                            )

                    new_ppe_overlays = []

                    for index, (px1, py1, px2, py2) in enumerate(persons):
                        present = set()
                        negatives = set()

                        for (label, conf, x1, y1, x2, y2) in items:
                            cx = (x1 + x2) // 2
                            cy = (y1 + y2) // 2

                            if px1 <= cx <= px2 and py1 <= cy <= py2:
                                present.add(label)
                                if label.startswith(negative_ppe_prefix):
                                    negatives.add(label)    

                        if negatives:
                            normalized_negatives = sorted([x.upper() for x in negatives])
                            msg = overlay_text["ppe_incorrect_prefix"] + ", ".join(
                                sorted([x.upper() for x in negatives])
                            )

                            new_ppe_overlays.append(
                                {
                                    "text": msg,
                                    "box": (px1, py1, px2, py2),
                                    "color": (0, 255, 255),
                                }
                            )

                            log_line(f"PPE: {msg}", key="ppe_neg", throttle_sec=1.5)

                            event_key = build_event_key(
                                event_type="ppe_incorrect",
                                details="|".join(normalized_negatives),
                                zone=f"person_{index}",
                            )

                            if should_emit_event(event_key, ppe_event_cooldown):
                                try:
                                    create_security_event(
                                        event_type="ppe_incorrect",
                                        details=msg,
                                        frame=frame,
                                    )
                                except Exception as e:
                                    log_line(
                                        f"❌ Error guardando PPE incorrecto: {e}",
                                        key="db_ppe_incorrect_err",
                                        throttle_sec=5,
                                    )
                            continue

                        missing = sorted(list(required_ppe_labels - present))

                        if missing:
                            msg = overlay_text["ppe_missing_prefix"] + ", ".join(missing)

                            new_ppe_overlays.append(
                                {
                                    "text": msg,
                                    "box": (px1, py1, px2, py2),
                                    "color": (0, 255, 255),
                                }
                            )

                            log_line(f"PPE: {msg}", key="ppe_missing", throttle_sec=1.5)

                            event_key = build_event_key(
                                event_type="ppe_missing",
                                details="|".join(missing),
                                zone=f"person_{index}",
                            )

                            if should_emit_event(event_key, ppe_event_cooldown):
                                try:
                                    create_security_event(
                                        event_type="ppe_missing",
                                        details=msg,
                                        frame=frame,
                                    )
                                except Exception as e:
                                    log_line(
                                        f"❌ Error guardando PPE faltante: {e}",
                                        key="db_ppe_missing_err",
                                        throttle_sec=5,
                                    )
                        else:
                            new_ppe_overlays.append(
                                {
                                    "text": overlay_text["ppe_ok"],
                                    "box": (px1, py1, px2, py2),
                                    "color": (0, 255, 0),
                                }
                            )

                    cached_ppe_overlays = new_ppe_overlays

                except Exception as e:
                    log_line(f"❌ Error PPE detect: {e}", key="ppe_detect_err", throttle_sec=5)

                last_ppe_run = now

            # Dibujar overlays cacheados
            for (x, y, w, h) in cached_faces:
                cv2.rectangle(display_frame, (x, y), (x + w, y + h), (255, 0, 0), 2)

            for item in cached_danger_items:
                x, y, w, h = item["box"]
                label = item["label"]
                conf = item["conf"]

                cv2.rectangle(display_frame, (x, y), (x + w, y + h), (0, 0, 255), 2)
                cv2.putText(
                    display_frame,
                    f"{label}: {conf:.2f}",
                    (x, max(y - 10, 20)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.55,
                    (0, 0, 255),
                    2,
                )

            for overlay in cached_ppe_overlays:
                px1, py1, px2, py2 = overlay["box"]
                color = overlay["color"]
                text = overlay["text"]

                cv2.rectangle(display_frame, (px1, py1), (px2, py2), color, 2)
                cv2.putText(
                    display_frame,
                    text,
                    (px1, max(py1 - 10, 20)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.6,
                    color,
                    2,
                )

            output_frame = cv2.resize(
                display_frame,
                (output_width, output_height),
                interpolation=cv2.INTER_LINEAR,
            )

            ret, buffer = cv2.imencode(
                ".jpg",
                output_frame,
                [int(cv2.IMWRITE_JPEG_QUALITY), jpeg_quality],
            )
            if not ret:
                continue

            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n" + buffer.tobytes() + b"\r\n"
            )

            elapsed = time.monotonic() - loop_started
            sleep_left = frame_interval - elapsed
            if sleep_left > 0:
                time.sleep(sleep_left)

    finally:
        stream.stop()
        log_line("🟠 Streaming detenido", key="stream_stop", throttle_sec=2)