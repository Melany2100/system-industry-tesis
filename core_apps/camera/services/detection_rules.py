from django.core.cache import cache
from core_apps.camera.models import DetectionRule


RULES_CACHE_KEY = "active_detection_rules_v2"
RULES_CACHE_TIMEOUT = 20


RISK_COLORS = {
    "CRITICO": (0, 0, 255),      # rojo
    "ALTO": (0, 80, 255),        # naranja/rojo
    "MEDIO": (0, 165, 255),      # naranja
    "BAJO": (0, 255, 0),         # verde
}


DEFAULT_MONITORED_LABELS = {
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
}


def clear_detection_rules_cache():
    cache.delete(RULES_CACHE_KEY)


def get_active_detection_rules():
    """
    Devuelve un diccionario por model_label y aliases.
    Se cachea pocos segundos para no consultar la BD en cada frame.
    """
    cached_rules = cache.get(RULES_CACHE_KEY)
    if cached_rules is not None:
        return cached_rules

    rules = {}
    queryset = DetectionRule.objects.filter(is_active=True)

    for rule in queryset:
        rules[rule.model_label.lower().strip()] = rule
        for alias in rule.get_aliases_list():
            rules[alias.lower().strip()] = rule

    cache.set(RULES_CACHE_KEY, rules, timeout=RULES_CACHE_TIMEOUT)
    return rules


def get_detection_rule(label):
    if not label:
        return None
    rules = get_active_detection_rules()
    return rules.get(str(label).lower().strip())


def get_monitored_model_labels():
    """
    Lista de etiquetas que el sistema debe observar.
    Incluye etiquetas por defecto para que la cámara funcione aunque todavía no se haya ejecutado el seed.
    """
    labels = set(DEFAULT_MONITORED_LABELS)
    labels.update(
        DetectionRule.objects.filter(is_active=True).values_list("model_label", flat=True)
    )
    return {str(label).lower().strip() for label in labels if label}


def get_rule_color(rule):
    if rule is None:
        return (255, 255, 255)
    return RISK_COLORS.get(rule.risk_level, (255, 255, 255))


def build_detection_details(rule, confidence, duration_seconds=None):
    details = (
        f"{rule.name} detectado | "
        f"Categoría: {rule.get_category_display()} | "
        f"Nivel: {rule.risk_level} | "
        f"Confianza: {confidence:.2f}"
    )

    if duration_seconds is not None:
        details += f" | Tiempo de uso: {int(duration_seconds)} segundos"

    if rule.description:
        details += f" | {rule.description}"

    return details


def should_generate_detection_event(rule, confidence, duration_seconds=None):
    """
    Determina si se debe guardar un evento.
    Importante: should_alert=False NO impide guardar el evento; solo indica que no debe mostrarse como alerta.
    """
    if rule is None:
        return False

    if not rule.is_active:
        return False

    if confidence < rule.min_confidence:
        return False

    if rule.requires_duration:
        if duration_seconds is None:
            return False
        return duration_seconds >= rule.max_allowed_seconds

    return True
