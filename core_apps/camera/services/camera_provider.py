from __future__ import annotations

import os
import time
from threading import Lock, Thread


def safe_import_cv2():
    try:
        import cv2  # type: ignore
        return cv2
    except Exception:
        return None


def probe_camera():
    cv2 = safe_import_cv2()
    if cv2 is None:
        return None, "OpenCV no está instalado."

    try:
        if os.name == "nt":
            cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
        else:
            cap = cv2.VideoCapture(0)

        try:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass

        if not cap or not cap.isOpened():
            if cap:
                cap.release()
            return None, "No se encontró una cámara disponible o está siendo usada por otra aplicación."

        return cap, None
    except Exception as e:
        return None, f"Error al intentar abrir la cámara: {str(e)}"


class CameraStream:
    def __init__(self, device_index: int = 0, width: int = 640, height: int = 480, target_fps: int = 20):
        self.device_index = device_index
        self.width = width
        self.height = height
        self.target_fps = target_fps

        self.cap = None
        self.running = False
        self.thread = None

        self._lock = Lock()
        self._frame = None
        self._last_ok_at = 0.0

    def start(self):
        cv2 = safe_import_cv2()
        if cv2 is None:
            return None, "OpenCV no está instalado."

        try:
            if os.name == "nt":
                self.cap = cv2.VideoCapture(self.device_index, cv2.CAP_DSHOW)
            else:
                self.cap = cv2.VideoCapture(self.device_index)

            if not self.cap or not self.cap.isOpened():
                self.stop()
                return None, "No se encontró una cámara disponible o está siendo usada por otra aplicación."

            try:
                self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
                self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
            except Exception:
                pass

            try:
                self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            except Exception:
                pass

            try:
                self.cap.set(cv2.CAP_PROP_FPS, self.target_fps)
            except Exception:
                pass

            self.running = True
            self.thread = Thread(target=self._reader_loop, daemon=True)
            self.thread.start()

            return self, None
        except Exception as e:
            self.stop()
            return None, f"Error al iniciar stream de cámara: {str(e)}"

    def _reader_loop(self):
        while self.running and self.cap is not None:
            ok, frame = self.cap.read()

            if ok and frame is not None:
                with self._lock:
                    self._frame = frame
                    self._last_ok_at = time.monotonic()
            else:
                time.sleep(0.01)

    def read(self):
        with self._lock:
            if self._frame is None:
                return False, None
            return True, self._frame.copy()

    def stop(self):
        self.running = False

        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=0.3)

        if self.cap is not None:
            try:
                self.cap.release()
            except Exception:
                pass

        self.cap = None
        self.thread = None


def open_camera_stream():
    stream = CameraStream(device_index=0, width=480, height=360, target_fps=15)
    return stream.start()