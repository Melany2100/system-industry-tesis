"""Publicación asíncrona de fotogramas procesados hacia MediaMTX."""

from __future__ import annotations

import logging
import shutil
import subprocess
import threading
from typing import Optional
from urllib.parse import urlsplit, urlunsplit

from django.conf import settings


logger = logging.getLogger(__name__)


class MediaMTXPublisher:
    """Envía el JPEG más reciente a FFmpeg sin bloquear la detección."""

    def __init__(self, stream_path: str, fps: int):
        base_url = settings.MEDIAMTX_PUBLISH_BASE_URL.rstrip("/")
        self.output_url = f"{base_url}/{stream_path}"
        self.display_url = self._redacted_url(self.output_url)
        self.fps = max(1, min(int(fps), 30))
        self.ffmpeg_binary = settings.FFMPEG_BINARY

        self._condition = threading.Condition()
        self._pending_frame: Optional[bytes] = None
        self._stop_event = threading.Event()
        self._process_lock = threading.Lock()
        self._process: Optional[subprocess.Popen] = None
        self._thread = threading.Thread(
            target=self._run,
            name=f"mediamtx-publisher-{stream_path}",
            daemon=True,
        )

    def start(self) -> None:
        if shutil.which(self.ffmpeg_binary) is None:
            logger.error(
                "No se encontró FFmpeg (%s); el stream %s no se publicará.",
                self.ffmpeg_binary,
                self.display_url,
            )
            return
        self._thread.start()

    def publish(self, jpeg_bytes: bytes) -> None:
        if self._stop_event.is_set() or not self._thread.is_alive():
            return

        # Cola de tamaño uno: si la red se retrasa, se descartan fotogramas
        # antiguos en vez de frenar OpenCV y la lógica de detección.
        with self._condition:
            self._pending_frame = jpeg_bytes
            self._condition.notify()

    def stop(self) -> None:
        if self._stop_event.is_set():
            return

        self._stop_event.set()
        with self._condition:
            self._condition.notify_all()
        self._terminate_process()

        if self._thread.is_alive():
            self._thread.join(timeout=3)

    def _command(self) -> list[str]:
        keyframe_interval = max(self.fps * 2, 10)
        return [
            self.ffmpeg_binary,
            "-hide_banner",
            "-loglevel",
            "warning",
            "-f",
            "image2pipe",
            "-vcodec",
            "mjpeg",
            "-framerate",
            str(self.fps),
            "-i",
            "pipe:0",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-tune",
            "zerolatency",
            "-pix_fmt",
            "yuv420p",
            "-g",
            str(keyframe_interval),
            "-f",
            "rtsp",
            "-rtsp_transport",
            "tcp",
            self.output_url,
        ]

    @staticmethod
    def _redacted_url(url: str) -> str:
        parsed = urlsplit(url)
        hostname = parsed.hostname or ""
        if parsed.port:
            hostname = f"{hostname}:{parsed.port}"
        netloc = hostname
        if parsed.username:
            netloc = f"{parsed.username}:***@{hostname}"
        return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))

    def _start_process(self) -> Optional[subprocess.Popen]:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            process = subprocess.Popen(
                self._command(),
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=None,
                creationflags=creationflags,
            )
        except OSError as exc:
            logger.error("No se pudo iniciar FFmpeg para %s: %s", self.display_url, exc)
            return None

        with self._process_lock:
            self._process = process
        logger.info("Publicación MediaMTX iniciada: %s", self.display_url)
        return process

    def _terminate_process(self) -> None:
        with self._process_lock:
            process = self._process
            self._process = None

        if process is None:
            return

        try:
            if process.stdin:
                process.stdin.close()
        except OSError:
            pass

        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()

    def _next_frame(self) -> Optional[bytes]:
        with self._condition:
            self._condition.wait_for(
                lambda: self._pending_frame is not None or self._stop_event.is_set(),
                timeout=1,
            )
            frame = self._pending_frame
            self._pending_frame = None
            return frame

    def _run(self) -> None:
        process: Optional[subprocess.Popen] = None

        while not self._stop_event.is_set():
            frame = self._next_frame()
            if frame is None:
                continue

            if process is None or process.poll() is not None:
                self._terminate_process()
                process = self._start_process()
                if process is None:
                    self._stop_event.wait(2)
                    continue

            try:
                if process.stdin is None:
                    raise BrokenPipeError("FFmpeg no abrió stdin")
                process.stdin.write(frame)
                process.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                logger.warning("MediaMTX desconectado (%s): %s", self.display_url, exc)
                self._terminate_process()
                process = None

        self._terminate_process()


def build_mediamtx_publisher(camera, fps: int) -> Optional[MediaMTXPublisher]:
    if not settings.MEDIAMTX_PUBLISH_BASE_URL:
        return None

    return MediaMTXPublisher(camera.get_stream_path(), fps)
