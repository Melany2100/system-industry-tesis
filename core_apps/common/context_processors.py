from .permissions import get_authorized_person_for_user, get_user_role, is_admin_user


def user_role(request):
    user = getattr(request, "user", None)
    authorized_person = get_authorized_person_for_user(user)

    return {
        "current_user_role": get_user_role(user),
        "is_admin_user": is_admin_user(user),
        "current_authorized_person": authorized_person,
    }
