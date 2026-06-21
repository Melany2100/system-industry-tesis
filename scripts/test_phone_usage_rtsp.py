import os
import time
import cv2
from ultralytics import YOLO


RTSP_URL = os.getenv(
    "RTSP_URL",
    "rtsp://admin:123445677@192.168.10.198:554/stream2"
)

MODEL_PATH = "yolov8n.pt"

PERSON_CLASS_ID = 0
PHONE_CLASS_ID = 67

CONF = 0.25
IMGSZ = 640

DETECT_EVERY_N_FRAMES = 3
ALERT_AFTER_SECONDS = 10
GRACE_SECONDS = 1.5
ALERT_COOLDOWN_SECONDS = 15

EVIDENCE_DIR = "pruebas_yolo/phone_usage_fast/evidencias"
os.makedirs(EVIDENCE_DIR, exist_ok=True)


def box_center(box):
    x1, y1, x2, y2 = box
    return ((x1 + x2) / 2, (y1 + y2) / 2)


def point_inside_box(point, box):
    px, py = point
    x1, y1, x2, y2 = box
    return x1 <= px <= x2 and y1 <= py <= y2


def save_evidence(frame, elapsed):
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    filename = f"cell_phone_usage_{int(elapsed)}s_{timestamp}.jpg"
    path = os.path.join(EVIDENCE_DIR, filename)
    cv2.imwrite(path, frame)
    return path


model = YOLO(MODEL_PATH)

cap = cv2.VideoCapture(RTSP_URL, cv2.CAP_FFMPEG)
cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

if not cap.isOpened():
    print("No se pudo abrir la cámara RTSP.")
    raise SystemExit

phone_usage_start = None
last_phone_seen = None
last_alert_time = 0

frame_count = 0
last_person_boxes = []
last_phone_boxes = []

print("Prueba rápida de uso de celular iniciada.")
print("Presiona Q para cerrar.")

while True:
    ret, frame = cap.read()

    if not ret:
        print("No se pudo leer frame.")
        time.sleep(0.2)
        continue

    frame_count += 1
    now = time.time()

    if frame_count % DETECT_EVERY_N_FRAMES == 0:
        results = model.predict(
            source=frame,
            conf=CONF,
            imgsz=IMGSZ,
            classes=[PERSON_CLASS_ID, PHONE_CLASS_ID],
            verbose=False
        )

        person_boxes = []
        phone_boxes = []

        if results and results[0].boxes is not None:
            for box in results[0].boxes:
                cls_id = int(box.cls[0])
                conf = float(box.conf[0])
                x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())

                if cls_id == PERSON_CLASS_ID:
                    person_boxes.append((x1, y1, x2, y2, conf))

                elif cls_id == PHONE_CLASS_ID:
                    phone_boxes.append((x1, y1, x2, y2, conf))

        last_person_boxes = person_boxes
        last_phone_boxes = phone_boxes

    phone_in_use = False
    active_phone_box = None

    for phone in last_phone_boxes:
        phone_box = phone[:4]
        phone_center = box_center(phone_box)

        for person in last_person_boxes:
            person_box = person[:4]

            if point_inside_box(phone_center, person_box):
                phone_in_use = True
                active_phone_box = phone_box
                break

        if phone_in_use:
            break

    if phone_in_use:
        last_phone_seen = now

        if phone_usage_start is None:
            phone_usage_start = now

        elapsed = now - phone_usage_start

    else:
        if last_phone_seen is not None and now - last_phone_seen <= GRACE_SECONDS:
            elapsed = now - phone_usage_start if phone_usage_start else 0
        else:
            phone_usage_start = None
            last_phone_seen = None
            elapsed = 0

    for person in last_person_boxes:
        x1, y1, x2, y2, conf = person
        cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 180, 0), 2)
        cv2.putText(
            frame,
            f"person {conf:.2f}",
            (x1, max(y1 - 10, 20)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (0, 180, 0),
            2
        )

    for phone in last_phone_boxes:
        x1, y1, x2, y2, conf = phone
        color = (0, 0, 255) if phone_in_use else (0, 165, 255)

        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        cv2.putText(
            frame,
            f"cell_phone {conf:.2f}",
            (x1, max(y1 - 10, 20)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            color,
            2
        )

    cv2.putText(
        frame,
        f"Uso celular: {elapsed:.1f}s",
        (20, 40),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.9,
        (0, 0, 255) if phone_in_use else (255, 255, 255),
        2
    )

    if elapsed >= ALERT_AFTER_SECONDS:
        if now - last_alert_time >= ALERT_COOLDOWN_SECONDS:
            last_alert_time = now
            evidence_path = save_evidence(frame, elapsed)

            print("====================================")
            print("ALERTA: uso prolongado de celular")
            print(f"Tiempo: {elapsed:.1f} segundos")
            print(f"Evidencia: {evidence_path}")
            print("====================================")

        cv2.putText(
            frame,
            "ALERTA: USO PROLONGADO DE CELULAR",
            (20, 80),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (0, 0, 255),
            2
        )

    cv2.imshow("Uso de celular - version rapida", frame)

    if cv2.waitKey(1) & 0xFF == ord("q"):
        break

cap.release()
cv2.destroyAllWindows()