from __future__ import annotations

ANALYSIS_CONFIG = {
    # Stream
    "stream_fps": 12,
    "output_width": 480,
    "output_height": 360,
    "jpeg_quality": 40,

    # Resolución de análisis
    "analysis_width": 256,
    "analysis_height": 192,

    # Intervalos de análisis (segundos)
    "face_interval": 0.45,
    "danger_interval": 1.20,
    "ppe_interval": 2.50,

    # Cooldowns de eventos (segundos)
    "danger_event_cooldown": 6.0,
    "ppe_event_cooldown": 8.0,

    # Umbrales
    "danger_conf_threshold": 0.55,
    "ppe_conf_threshold": 0.65,

    # Reglas PPE
    "required_ppe_labels": ["hardhat", "safety vest", "mask"],
    "negative_ppe_prefix": "no-",

    # Clases peligrosas
    "dangerous_classes": ["knife", "gun", "scissors", "bottle"],

    # Reglas de asociación persona-item
    "person_item_match_mode": "center_point",

    # Texto visible
    "overlay_text": {
        "ppe_ok": "EPP OK",
        "ppe_missing_prefix": "⚠ Falta EPP: ",
        "ppe_incorrect_prefix": "⚠ Indumentaria incorrecta: ",
        "danger_prefix": "Objeto peligroso",
    },
}