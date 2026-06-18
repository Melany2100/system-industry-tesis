import unicodedata

from django.db.models import Q

from core_apps.camera.models import AuthorizedPerson


ADMIN_GROUP_NAMES = {"admin", "admins", "administrador", "administradores"}
JEFE_GROUP_NAMES = {"jefe", "jefes"}
OPERADOR_GROUP_NAMES = {"operador", "operadores"}


def is_system_admin(user):
    if not user or not user.is_authenticated:
        return False

    if user.is_superuser:
        return True

    group_names = get_normalized_group_names(user)

    return bool(group_names & ADMIN_GROUP_NAMES)


def is_jefe_user(user):
    if not user or not user.is_authenticated:
        return False

    if is_system_admin(user):
        return False

    return bool(get_normalized_group_names(user) & JEFE_GROUP_NAMES)


def can_manage_users(user):
    return is_system_admin(user) or is_jefe_user(user)


def can_access_management(user):
    return is_system_admin(user) or is_jefe_user(user)


def is_admin_user(user):
    return can_access_management(user)


def is_operator_user(user):
    if not user or not user.is_authenticated:
        return False

    return get_user_role(user) == "operador"


def get_user_role(user):
    if is_system_admin(user):
        return "admin"

    if is_jefe_user(user):
        return "jefe"

    group_names = get_normalized_group_names(user)

    if group_names & OPERADOR_GROUP_NAMES:
        return "operador"

    return "operador"


def get_user_role_label(user):
    role = get_user_role(user)

    if role == "admin":
        return "Admin"

    if role == "jefe":
        return "Jefe"

    return "Operador"


def get_normalized_group_names(user):
    if not user or not user.is_authenticated:
        return set()

    return {
        _normalize_identity(name)
        for name in user.groups.values_list("name", flat=True)
    }


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
