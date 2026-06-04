from django.core.management.base import BaseCommand
from core_apps.camera.models import DetectionRule
from core_apps.camera.services.detection_rules import clear_detection_rules_cache


class Command(BaseCommand):
    help = "Crea o actualiza las reglas iniciales de detección de objetos del SMRI."

    def handle(self, *args, **options):
        rules = [
            # ============================================================
            # CRITICO: objetos afilados que pueden causar heridas graves
            # ============================================================
            {
                "name": "Cuchillo",
                "model_label": "knife",
                "aliases": "cuchillo, arma blanca, objeto afilado",
                "category": "dangerous",
                "risk_level": "CRITICO",
                "event_type": "dangerous_object",
                "should_alert": True,
                "min_confidence": 0.60,
                "cooldown_seconds": 15,
                "description": "Objeto afilado que puede causar heridas graves o incluso la muerte.",
            },
            {
                "name": "Tijeras",
                "model_label": "scissors",
                "aliases": "tijeras, objeto cortante, objeto afilado",
                "category": "dangerous",
                "risk_level": "CRITICO",
                "event_type": "dangerous_object",
                "should_alert": True,
                "min_confidence": 0.60,
                "cooldown_seconds": 15,
                "description": "Objeto cortante que puede causar heridas.",
            },
            {
                "name": "Objeto afilado personalizado",
                "model_label": "sharp_object",
                "aliases": "objeto afilado, punzante, cortante",
                "category": "dangerous",
                "risk_level": "CRITICO",
                "event_type": "dangerous_object",
                "should_alert": True,
                "min_confidence": 0.65,
                "cooldown_seconds": 15,
                "description": "Clase pensada para un modelo personalizado entrenado con objetos afilados.",
            },

            # ============================================================
            # ALTO: objetos/sustancias no filosas que pueden intoxicar
            # ============================================================
            {
                "name": "Botella o envase sospechoso",
                "model_label": "bottle",
                "aliases": "botella, envase, bebida, bebida alcohólica",
                "category": "dangerous",
                "risk_level": "ALTO",
                "event_type": "dangerous_object",
                "should_alert": True,
                "min_confidence": 0.55,
                "cooldown_seconds": 20,
                "description": "COCO detecta botellas de forma general. Para distinguir alcohol o químicos se recomienda modelo personalizado.",
            },
            {
                "name": "Sustancia tóxica o contaminante",
                "model_label": "toxic_container",
                "aliases": "sustancia tóxica, químico, envase químico, intoxicación",
                "category": "dangerous",
                "risk_level": "ALTO",
                "event_type": "dangerous_object",
                "should_alert": True,
                "min_confidence": 0.65,
                "cooldown_seconds": 20,
                "description": "Clase recomendada para modelo personalizado.",
            },
            {
                "name": "Bebida alcohólica",
                "model_label": "alcoholic_beverage",
                "aliases": "alcohol, cerveza, licor, bebida alcohólica",
                "category": "unauthorized",
                "risk_level": "ALTO",
                "event_type": "unauthorized_access",
                "should_alert": True,
                "min_confidence": 0.65,
                "cooldown_seconds": 20,
                "description": "Clase recomendada para modelo personalizado. COCO no distingue alcohol de otras botellas.",
            },
            {
                "name": "Objeto contundente",
                "model_label": "baseball bat",
                "aliases": "bate, palo, objeto contundente",
                "category": "dangerous",
                "risk_level": "ALTO",
                "event_type": "dangerous_object",
                "should_alert": True,
                "min_confidence": 0.55,
                "cooldown_seconds": 20,
                "description": "Objeto no filoso que puede causar lesiones por golpe.",
            },

            # ============================================================
            # MEDIO: se registra, pero NO genera alerta
            # ============================================================
            {
                "name": "Estilete",
                "model_label": "box_cutter",
                "aliases": "estilete, cutter, cortador",
                "category": "dangerous",
                "risk_level": "MEDIO",
                "event_type": "dangerous_object",
                "should_alert": False,
                "min_confidence": 0.60,
                "cooldown_seconds": 30,
                "description": "Riesgo medio. Se registra para trazabilidad, pero no genera alerta.",
            },
            {
                "name": "Aceite",
                "model_label": "oil_container",
                "aliases": "aceite, envase de aceite",
                "category": "dangerous",
                "risk_level": "MEDIO",
                "event_type": "dangerous_object",
                "should_alert": False,
                "min_confidence": 0.60,
                "cooldown_seconds": 30,
                "description": "Riesgo medio. Requiere modelo personalizado para precisión.",
            },
            {
                "name": "Madera",
                "model_label": "wood",
                "aliases": "madera, tabla, pedazo de madera",
                "category": "dangerous",
                "risk_level": "MEDIO",
                "event_type": "dangerous_object",
                "should_alert": False,
                "min_confidence": 0.60,
                "cooldown_seconds": 30,
                "description": "Riesgo medio. Requiere modelo personalizado para precisión.",
            },
            {
                "name": "Cemento de contacto",
                "model_label": "contact_cement",
                "aliases": "cemento de contacto, pegamento industrial, adhesivo",
                "category": "dangerous",
                "risk_level": "MEDIO",
                "event_type": "dangerous_object",
                "should_alert": False,
                "min_confidence": 0.60,
                "cooldown_seconds": 30,
                "description": "Riesgo medio. Se registra, pero no genera alerta.",
            },
            {
                "name": "Quita grasa",
                "model_label": "degreaser",
                "aliases": "quita grasa, desengrasante, limpiador industrial",
                "category": "dangerous",
                "risk_level": "MEDIO",
                "event_type": "dangerous_object",
                "should_alert": False,
                "min_confidence": 0.60,
                "cooldown_seconds": 30,
                "description": "Riesgo medio. Se registra, pero no genera alerta.",
            },

            # ============================================================
            # BAJO: bajo riesgo / permitido / no alerta
            # ============================================================
            {
                "name": "Pintura",
                "model_label": "paint_can",
                "aliases": "pintura, lata de pintura",
                "category": "allowed",
                "risk_level": "BAJO",
                "event_type": "authorized_object",
                "should_alert": False,
                "min_confidence": 0.60,
                "cooldown_seconds": 30,
                "description": "Objeto de bajo riesgo. No genera alerta.",
            },
            {
                "name": "Pistola de silicona",
                "model_label": "glue_gun",
                "aliases": "pistola de silicona, silicona caliente",
                "category": "allowed",
                "risk_level": "BAJO",
                "event_type": "authorized_object",
                "should_alert": False,
                "min_confidence": 0.60,
                "cooldown_seconds": 30,
                "description": "Objeto de bajo riesgo. No genera alerta.",
            },
            {
                "name": "Guantes",
                "model_label": "gloves",
                "aliases": "guantes, guante",
                "category": "ppe",
                "risk_level": "BAJO",
                "event_type": "authorized_object",
                "should_alert": False,
                "min_confidence": 0.50,
                "cooldown_seconds": 30,
                "description": "EPP permitido. No genera alerta.",
            },

            # ============================================================
            # OBJETOS NO AUTORIZADOS
            # ============================================================
            {
                "name": "Uso excesivo de celular",
                "model_label": "cell phone",
                "aliases": "celular, teléfono, smartphone, telefono",
                "category": "unauthorized",
                "risk_level": "ALTO",
                "event_type": "unauthorized_access",
                "should_alert": True,
                "min_confidence": 0.55,
                "cooldown_seconds": 30,
                "requires_duration": True,
                "max_allowed_seconds": 60,
                "description": "Solo registra alerta cuando el celular permanece detectado más del tiempo permitido.",
            },
            {
                "name": "Mochila",
                "model_label": "backpack",
                "aliases": "mochila",
                "category": "unauthorized",
                "risk_level": "ALTO",
                "event_type": "unauthorized_access",
                "should_alert": True,
                "min_confidence": 0.55,
                "cooldown_seconds": 20,
                "description": "Objeto no autorizado en zona monitoreada.",
            },
            {
                "name": "Bolso",
                "model_label": "handbag",
                "aliases": "bolso, cartera",
                "category": "unauthorized",
                "risk_level": "ALTO",
                "event_type": "unauthorized_access",
                "should_alert": True,
                "min_confidence": 0.55,
                "cooldown_seconds": 20,
                "description": "Objeto no autorizado en zona monitoreada.",
            },
            {
                "name": "Maleta",
                "model_label": "suitcase",
                "aliases": "maleta, equipaje",
                "category": "unauthorized",
                "risk_level": "ALTO",
                "event_type": "unauthorized_access",
                "should_alert": True,
                "min_confidence": 0.55,
                "cooldown_seconds": 20,
                "description": "Objeto no autorizado en zona monitoreada.",
            },

            # ============================================================
            # ANIMALES
            # ============================================================
            {
                "name": "Perro",
                "model_label": "dog",
                "aliases": "perro, animal",
                "category": "animal",
                "risk_level": "ALTO",
                "event_type": "unauthorized_access",
                "should_alert": True,
                "min_confidence": 0.45,
                "cooldown_seconds": 20,
                "description": "Animal detectado en zona monitoreada.",
            },
            {
                "name": "Gato",
                "model_label": "cat",
                "aliases": "gato, animal",
                "category": "animal",
                "risk_level": "ALTO",
                "event_type": "unauthorized_access",
                "should_alert": True,
                "min_confidence": 0.45,
                "cooldown_seconds": 20,
                "description": "Animal detectado en zona monitoreada.",
            },
            {
                "name": "Ave",
                "model_label": "bird",
                "aliases": "ave, pájaro, pajaro, animal",
                "category": "animal",
                "risk_level": "ALTO",
                "event_type": "unauthorized_access",
                "should_alert": True,
                "min_confidence": 0.45,
                "cooldown_seconds": 20,
                "description": "Animal detectado en zona monitoreada.",
            },
        ]

        created_count = 0
        updated_count = 0

        for data in rules:
            obj, created = DetectionRule.objects.update_or_create(
                model_label=data["model_label"],
                defaults=data,
            )
            if created:
                created_count += 1
            else:
                updated_count += 1

        clear_detection_rules_cache()

        self.stdout.write(
            self.style.SUCCESS(
                f"Reglas listas. Creadas: {created_count}. Actualizadas: {updated_count}."
            )
        )
