import json

from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from .models import UserSetting


def get_user_settings(user):
    settings_obj, _ = UserSetting.objects.get_or_create(user=user)
    return settings_obj


def get_json_body(request):
    try:
        return json.loads(request.body.decode("utf-8"))
    except Exception:
        return {}


@login_required
@require_POST
def update_profile(request):
    data = get_json_body(request)

    username = data.get("username", "").strip()
    full_name = data.get("full_name", "").strip()

    if not username:
        return JsonResponse({"success": False, "message": "El nombre de usuario es obligatorio."}, status=400)

    exists = User.objects.filter(username=username).exclude(id=request.user.id).exists()

    if exists:
        return JsonResponse({"success": False, "message": "Este nombre de usuario ya está en uso."}, status=400)

    names = full_name.split(" ", 1)

    request.user.username = username
    request.user.first_name = names[0] if len(names) >= 1 else ""
    request.user.last_name = names[1] if len(names) == 2 else ""
    request.user.save()

    return JsonResponse({"success": True, "message": "Perfil actualizado correctamente."})


@login_required
@require_POST
def update_email(request):
    data = get_json_body(request)

    new_email = data.get("new_email", "").strip().lower()

    if not new_email:
        return JsonResponse({"success": False, "message": "Ingresa el nuevo correo."}, status=400)

    exists = User.objects.filter(email=new_email).exclude(id=request.user.id).exists()

    if exists:
        return JsonResponse({"success": False, "message": "Este correo ya está registrado."}, status=400)

    request.user.email = new_email
    request.user.save()

    return JsonResponse({"success": True, "message": "Correo actualizado correctamente."})


@login_required
@require_POST
def update_password(request):
    data = get_json_body(request)

    current_password = data.get("current_password", "")
    new_password = data.get("new_password", "")
    confirm_password = data.get("confirm_password", "")

    if not request.user.check_password(current_password):
        return JsonResponse({"success": False, "message": "La contraseña actual no es correcta."}, status=400)

    if new_password != confirm_password:
        return JsonResponse({"success": False, "message": "Las contraseñas nuevas no coinciden."}, status=400)

    try:
        validate_password(new_password, request.user)
    except ValidationError as e:
        return JsonResponse({"success": False, "message": " ".join(e.messages)}, status=400)

    request.user.set_password(new_password)
    request.user.save()

    update_session_auth_hash(request, request.user)

    return JsonResponse({"success": True, "message": "Contraseña actualizada correctamente."})


@login_required
@require_POST
def update_appearance(request):
    data = get_json_body(request)

    settings_obj = get_user_settings(request.user)

    settings_obj.theme_color = data.get("theme_color", "azul")
    settings_obj.compact_layout = bool(data.get("compact_layout", False))
    settings_obj.dark_mode = bool(data.get("dark_mode", False))
    settings_obj.save()

    return JsonResponse({"success": True, "message": "Apariencia actualizada correctamente."})


@login_required
@require_POST
def update_notifications(request):
    data = get_json_body(request)

    settings_obj = get_user_settings(request.user)

    settings_obj.security_alerts = bool(data.get("security_alerts", True))
    settings_obj.email_alerts = bool(data.get("email_alerts", False))
    settings_obj.save()

    return JsonResponse({"success": True, "message": "Notificaciones actualizadas correctamente."})


@login_required
@require_POST
def update_security(request):
    data = get_json_body(request)

    settings_obj = get_user_settings(request.user)

    settings_obj.extra_verification = bool(data.get("extra_verification", False))
    settings_obj.save()

    return JsonResponse({"success": True, "message": "Configuración de seguridad actualizada correctamente."})