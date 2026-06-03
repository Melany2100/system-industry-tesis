import unicodedata

from django.db.models import Q

from core_apps.camera.models import AuthorizedPerson


ADMIN_GROUP_NAMES = {"admin", "admins", "administrador", "administradores"}


def is_admin_user(user):
    if not user or not user.is_authenticated:
        return False

    if user.is_superuser or user.is_staff:
        return True

    group_names = {
        _normalize_identity(name)
        for name in user.groups.values_list("name", flat=True)
    }

    return bool(group_names & ADMIN_GROUP_NAMES)


def get_user_role(user):
    return "admin" if is_admin_user(user) else "operator"


def _normalize_identity(value):
    value = str(value or "").strip().lower()
    normalized = unicodedata.normalize("NFKD", value)
    return "".join(char for char in normalized if not unicodedata.combining(char))


def get_authorized_person_for_user(user):
    if not user or not user.is_authenticated:
        return None

    email = (user.email or "").strip()
    username = (user.username or "").strip()

    exact_query = Q()

    if email:
        exact_query |= Q(correo__iexact=email)

    if username:
        exact_query |= Q(correo__iexact=username)

    if exact_query:
        person = AuthorizedPerson.objects.filter(exact_query, is_active=True).first()

        if person:
            return person

    user_names = {
        _normalize_identity(user.get_full_name()),
        _normalize_identity(username),
    }
    user_names.discard("")

    for person in AuthorizedPerson.objects.filter(is_active=True):
        person_names = {
            _normalize_identity(person.get_full_name()),
            _normalize_identity(person.nombres),
            _normalize_identity(person.correo),
        }

        if user_names & person_names:
            return person

    return None
