import json

from django.views.generic import TemplateView
from django.shortcuts import render
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.models import Group, User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from django.utils import timezone
from django.db.models import Count, Q
from django.db.models.functions import TruncDate
from datetime import timedelta

from core_apps.camera.models import SecurityEvent, AuthorizedPerson
from core_apps.common.models import UserSetting
from core_apps.common.permissions import (
    can_manage_users,
    get_authorized_person_for_user,
    get_user_role_label,
    is_admin_user,
    is_system_admin,
)
from core_apps.informes.models import Informe

from django.contrib.auth.decorators import login_required

SYSTEM_ROLE_GROUPS = {
    "admin": "Administrador",
    "operador": "Operador",
    "jefe": "Jefe",
}


def _ensure_system_groups():
    return {
        role: Group.objects.get_or_create(name=group_name)[0]
        for role, group_name in SYSTEM_ROLE_GROUPS.items()
    }


def _assign_system_role(user, role):
    groups = _ensure_system_groups()
    selected_group = groups[role]
    user.groups.remove(*groups.values())
    user.groups.add(selected_group)

    if role == "admin":
        user.is_staff = True
        user.is_superuser = True
    elif role == "jefe":
        user.is_staff = False
        user.is_superuser = False
    else:
        user.is_staff = False
        user.is_superuser = False

    user.save(update_fields=["is_staff", "is_superuser"])


@login_required
def settings_view(request):
    user_settings, _ = UserSetting.objects.get_or_create(user=request.user)
    managed_users = []
    available_user_roles = []

    if can_manage_users(request.user):
        managed_users = [
            {
                "user": user,
                "role": get_user_role_label(user),
            }
            for user in User.objects.order_by("username")
        ]
        available_user_roles = _available_roles_for_user(request.user)

    return render(request, "home/settings.html", {
        "segment": "settings",
        "user_settings": user_settings,
        "managed_users": managed_users,
        "available_user_roles": available_user_roles,
    })


@login_required
def help_view(request):
    return render(request, "home/help.html", {
        "segment": "help",
    })


def _available_roles_for_user(user):
    roles = [
        ("operador", "Operador"),
        ("jefe", "Jefe"),
    ]

    if is_system_admin(user):
        roles.insert(0, ("admin", "Admin"))

    return roles


def get_json_body(request):
    try:
        return json.loads(request.body.decode("utf-8"))
    except (TypeError, ValueError, UnicodeDecodeError):
        return {}


@login_required
@require_POST
def settings_update_profile(request):
    data = get_json_body(request)
    username = data.get("username", "").strip()
    first_name = data.get("first_name", "").strip()
    last_name = data.get("last_name", "").strip()

    if not username:
        return JsonResponse({"success": False, "message": "El nombre de usuario es obligatorio."}, status=400)

    if User.objects.filter(username=username).exclude(pk=request.user.pk).exists():
        return JsonResponse({"success": False, "message": "Este nombre de usuario ya esta en uso."}, status=400)

    request.user.username = username
    request.user.first_name = first_name
    request.user.last_name = last_name
    request.user.save(update_fields=["username", "first_name", "last_name"])

    return JsonResponse({"success": True, "message": "Perfil actualizado correctamente."})


@login_required
@require_POST
def settings_update_email(request):
    data = get_json_body(request)
    new_email = data.get("new_email", "").strip().lower()

    if not new_email:
        return JsonResponse({"success": False, "message": "Ingresa el nuevo correo."}, status=400)

    try:
        validate_email(new_email)
    except ValidationError:
        return JsonResponse({"success": False, "message": "Ingresa un correo valido."}, status=400)

    if User.objects.filter(email=new_email).exclude(pk=request.user.pk).exists():
        return JsonResponse({"success": False, "message": "Este correo ya esta registrado."}, status=400)

    request.user.email = new_email
    request.user.save(update_fields=["email"])

    return JsonResponse({"success": True, "message": "Correo actualizado correctamente."})


@login_required
@require_POST
def settings_update_password(request):
    data = get_json_body(request)
    current_password = data.get("current_password", "")
    new_password = data.get("new_password", "")
    confirm_password = data.get("confirm_password", "")

    if not current_password or not new_password or not confirm_password:
        return JsonResponse({"success": False, "message": "Completa todos los campos de contrasena."}, status=400)

    if not request.user.check_password(current_password):
        return JsonResponse({"success": False, "message": "La contrasena actual no es correcta."}, status=400)

    if new_password != confirm_password:
        return JsonResponse({"success": False, "message": "Las contrasenas nuevas no coinciden."}, status=400)

    try:
        validate_password(new_password, request.user)
    except ValidationError as exc:
        return JsonResponse({"success": False, "message": " ".join(exc.messages)}, status=400)

    request.user.set_password(new_password)
    request.user.save(update_fields=["password"])
    update_session_auth_hash(request, request.user)

    return JsonResponse({"success": True, "message": "Contrasena actualizada correctamente."})


@login_required
@require_POST
def settings_create_user(request):
    if not can_manage_users(request.user):
        return JsonResponse({"success": False, "message": "Solo Admin o Jefe pueden crear usuarios."}, status=403)

    data = get_json_body(request)
    username = data.get("username", "").strip()
    first_name = data.get("first_name", "").strip()
    last_name = data.get("last_name", "").strip()
    email = data.get("email", "").strip().lower()
    password = data.get("password", "")
    role = data.get("role", "operador").strip().lower()

    if role not in SYSTEM_ROLE_GROUPS:
        return JsonResponse({"success": False, "message": "Selecciona un rol valido."}, status=400)

    if role == "admin" and not is_system_admin(request.user):
        return JsonResponse({"success": False, "message": "Solo un Admin puede crear otro Admin."}, status=403)

    if not username:
        return JsonResponse({"success": False, "message": "El usuario es obligatorio."}, status=400)

    if User.objects.filter(username=username).exists():
        return JsonResponse({"success": False, "message": "Este usuario ya existe."}, status=400)

    if email:
        try:
            validate_email(email)
        except ValidationError:
            return JsonResponse({"success": False, "message": "Ingresa un correo valido."}, status=400)

        if User.objects.filter(email=email).exists():
            return JsonResponse({"success": False, "message": "Este correo ya esta registrado."}, status=400)

    if not password:
        return JsonResponse({"success": False, "message": "La contrasena es obligatoria."}, status=400)

    user = User(
        username=username,
        first_name=first_name,
        last_name=last_name,
        email=email,
        is_active=True,
    )

    try:
        validate_password(password, user)
    except ValidationError as exc:
        return JsonResponse({"success": False, "message": " ".join(exc.messages)}, status=400)

    user.set_password(password)
    user.save()
    _assign_system_role(user, role)
    UserSetting.objects.get_or_create(user=user)

    return JsonResponse({
        "success": True,
        "message": f"Usuario creado correctamente con rol {get_user_role_label(user)}.",
        "user": {
            "id": user.id,
            "username": user.username,
            "full_name": user.get_full_name(),
            "email": user.email,
            "role": get_user_role_label(user),
        },
    })

class IndexView(LoginRequiredMixin, TemplateView):
    template_name = 'home/index.html'
    login_url = '/login/'

    def dispatch(self, request, *args, **kwargs):
        if not is_admin_user(request.user):
            from django.shortcuts import redirect

            return redirect("dashboard")

        return super().dispatch(request, *args, **kwargs)


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = 'home/dashboard.html'
    login_url = '/login/'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        today = timezone.localdate()
        now = timezone.localtime()
        events_qs = SecurityEvent.objects.all()
        reports_qs = Informe.objects.all()

        if not is_admin_user(self.request.user):
            authorized_person = get_authorized_person_for_user(self.request.user)

            if authorized_person is not None:
                identity_filter = Q(authorized_person=authorized_person)

                for value in (
                    authorized_person.get_full_name(),
                    authorized_person.nombres,
                    authorized_person.apellidos,
                    authorized_person.correo,
                ):
                    if value:
                        identity_filter |= Q(details__icontains=value)

                events_qs = events_qs.filter(identity_filter).exclude(authorized_person__isnull=True)
                reports_qs = reports_qs.filter(persona_detectada__icontains=authorized_person.get_full_name())
            else:
                identity_filter = Q(related_user=self.request.user)

                for value in (
                    self.request.user.get_full_name(),
                    self.request.user.first_name,
                    self.request.user.last_name,
                    self.request.user.username,
                    self.request.user.email,
                ):
                    value = (value or "").strip()

                    if value:
                        identity_filter |= Q(details__icontains=value)

                events_qs = events_qs.filter(identity_filter)
                reports_qs = reports_qs.filter(persona_detectada__icontains=self.request.user.username)

        # =========================
        # Métricas principales
        # =========================
        total_events_today = events_qs.filter(
            timestamp__date=today
        ).count()

        critical_alerts = events_qs.filter(
            resolved=False,
            timestamp__date=today,
            severity__in=['ALTO', 'CRITICO'],
        ).count()

        unauthorized_accesses_today = events_qs.filter(
            timestamp__date=today,
            event_type__in=[
                'unauthorized_access',
                'intrusion',
                'face_unknown',
            ],
        ).count()

        informes_today = reports_qs.filter(
            fecha__date=today
        ).count()

        total_reports = reports_qs.count()
        epp_ok_count = reports_qs.filter(epp_correcto=True).count()
        epp_incorrect_count = total_reports - epp_ok_count

        if total_reports > 0:
            epp_percent = round((epp_ok_count / total_reports) * 100)
            epp_incorrect_percent = 100 - epp_percent
        else:
            epp_percent = 0
            epp_incorrect_percent = 0

        authorized_people = AuthorizedPerson.objects.filter(
            is_active=True
        ).count()

        pending_events = events_qs.filter(
            resolved=False
        ).count()

        resolved_events = events_qs.filter(
            resolved=True
        ).count()

        total_events = events_qs.count()

        # =========================
        # Últimos registros
        # =========================
        recent_events = events_qs.select_related(
            'related_user'
        ).all()[:5]

        recent_reports = reports_qs.order_by('-fecha')[:5]

        last_event = events_qs.first()

        # =========================
        # Eventos últimos 7 días
        # =========================
        start_day = today - timedelta(days=6)
        days = [start_day + timedelta(days=i) for i in range(7)]

        raw_weekly = (
            events_qs
            .filter(timestamp__date__gte=start_day, timestamp__date__lte=today)
            .annotate(day=TruncDate('timestamp'))
            .values('day')
            .annotate(total=Count('id'))
        )

        weekly_map = {
            item['day']: item['total']
            for item in raw_weekly
        }

        weekly_labels = [
            day.strftime('%d/%m')
            for day in days
        ]

        weekly_values = [
            weekly_map.get(day, 0)
            for day in days
        ]

        # =========================
        # Distribución por tipo de evento
        # =========================
        event_type_display = dict(SecurityEvent.EVENT_TYPES)

        raw_distribution = (
            events_qs
            .values('event_type')
            .annotate(total=Count('id'))
        )

        distribution_totals = {
            item['event_type']: item['total']
            for item in raw_distribution
        }
        event_type_order = [
            'dangerous_object',
            'authorized_object',
            'unauthorized_object',
            'face_recognized',
            'face_unknown',
            'unauthorized_access',
            'intrusion',
            'ppe_missing',
        ]
        event_breakdown = []

        for event_type in event_type_order:
            total = distribution_totals.get(event_type, 0)

            if total:
                event_breakdown.append({
                    'key': event_type,
                    'label': event_type_display.get(event_type, event_type),
                    'total': total,
                })

        known_event_types = set(event_type_order)
        remaining_event_types = sorted(
            set(distribution_totals) - known_event_types,
            key=lambda event_type: event_type_display.get(event_type, event_type),
        )

        for event_type in remaining_event_types:
            event_breakdown.append({
                'key': event_type,
                'label': event_type_display.get(event_type, event_type),
                'total': distribution_totals[event_type],
            })

        event_labels = [
            item['label']
            for item in event_breakdown
        ]

        event_values = [
            item['total']
            for item in event_breakdown
        ]

        # Para evitar que el gráfico falle si aún no hay eventos
        if not event_labels:
            event_labels = ['Sin eventos']
            event_values = [1]

        context.update({
            'segment': 'dashboard',

            'today': today,
            'now': now,

            'total_events': total_events,
            'total_events_today': total_events_today,
            'critical_alerts': critical_alerts,
            'unauthorized_accesses_today': unauthorized_accesses_today,
            'informes_today': informes_today,

            'epp_percent': epp_percent,
            'epp_incorrect_percent': epp_incorrect_percent,
            'epp_ok_count': epp_ok_count,
            'epp_incorrect_count': epp_incorrect_count,

            'authorized_people': authorized_people,
            'pending_events': pending_events,
            'resolved_events': resolved_events,

            'recent_events': recent_events,
            'recent_reports': recent_reports,
            'last_event': last_event,

            'weekly_labels': weekly_labels,
            'weekly_values': weekly_values,

            'event_labels': event_labels,
            'event_values': event_values,
            'event_breakdown': event_breakdown,
        })

        return context
