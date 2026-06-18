from .permissions import (
    get_authorized_person_for_user,
    get_user_role,
    get_user_role_label,
    can_manage_users,
    is_admin_user,
    is_jefe_user,
    is_operator_user,
    is_system_admin,
)


def user_role(request):
    user = getattr(request, "user", None)
    authorized_person = get_authorized_person_for_user(user)

    return {
        "current_user_role": get_user_role(user),
        "current_user_role_label": get_user_role_label(user),
        "is_admin_user": is_admin_user(user),
        "is_system_admin": is_system_admin(user),
        "is_jefe_user": is_jefe_user(user),
        "is_operator_user": is_operator_user(user),
        "can_manage_users": can_manage_users(user),
        "current_authorized_person": authorized_person,
    }
