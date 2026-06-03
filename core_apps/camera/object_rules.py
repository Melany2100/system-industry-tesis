OBJECT_RULES = {
    # =========================
    # OBJETOS PELIGROSOS
    # =========================
    "knife": {
        "event_type": "dangerous_object",
        "category": "dangerous_object",
        "severity": "critical",
        "should_alert": True,
        "message": "Objeto crítico detectado: cuchillo",
        "color": (0, 0, 255),
    },
    "scissors": {
        "event_type": "dangerous_object",
        "category": "dangerous_object",
        "severity": "critical",
        "should_alert": True,
        "message": "Objeto crítico detectado: tijeras",
        "color": (0, 0, 255),
    },
    "baseball bat": {
        "event_type": "dangerous_object",
        "category": "dangerous_object",
        "severity": "high",
        "should_alert": True,
        "message": "Objeto contundente detectado",
        "color": (0, 0, 255),
    },
    "bottle": {
        "event_type": "dangerous_object",
        "category": "dangerous_object",
        "severity": "high",
        "should_alert": True,
        "message": "Botella detectada en zona monitoreada",
        "color": (0, 165, 255),
    },

    # =========================
    # OBJETOS NO AUTORIZADOS
    # =========================
    "cell phone": {
        "event_type": "unauthorized_object",
        "category": "unauthorized_object",
        "severity": "medium",
        "should_alert": True,
        "message": "Objeto no autorizado detectado: celular",
        "color": (0, 255, 255),
    },
    "backpack": {
        "event_type": "unauthorized_object",
        "category": "unauthorized_object",
        "severity": "medium",
        "should_alert": True,
        "message": "Objeto no autorizado detectado: mochila",
        "color": (0, 255, 255),
    },
    "handbag": {
        "event_type": "unauthorized_object",
        "category": "unauthorized_object",
        "severity": "medium",
        "should_alert": True,
        "message": "Objeto no autorizado detectado: bolso",
        "color": (0, 255, 255),
    },
    "suitcase": {
        "event_type": "unauthorized_object",
        "category": "unauthorized_object",
        "severity": "medium",
        "should_alert": True,
        "message": "Objeto no autorizado detectado: maleta",
        "color": (0, 255, 255),
    },

    # =========================
    # ANIMALES
    # =========================
    "dog": {
        "event_type": "animal_detected",
        "category": "animal",
        "severity": "medium",
        "should_alert": True,
        "message": "Animal detectado: perro",
        "color": (255, 0, 255),
    },
    "cat": {
        "event_type": "animal_detected",
        "category": "animal",
        "severity": "medium",
        "should_alert": True,
        "message": "Animal detectado: gato",
        "color": (255, 0, 255),
    },
    "bird": {
        "event_type": "animal_detected",
        "category": "animal",
        "severity": "low",
        "should_alert": False,
        "message": "Animal detectado: ave",
        "color": (255, 0, 255),
    },
}