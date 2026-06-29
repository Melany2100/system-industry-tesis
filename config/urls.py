from django.contrib import admin
from django.urls import path, include
from core_apps.common.permissions import is_system_admin
from core_apps.common.views import (
    DashboardView,
    IndexView,
    help_view,
    settings_create_user,
    settings_update_email,
    settings_update_password,
    settings_update_profile,
    settings_view,
)
from django.contrib.auth import views as auth_views
from django.conf import settings
from django.conf.urls.static import static

admin.site.has_permission = lambda request: (
    request.user.is_active and is_system_admin(request.user)
)

urlpatterns = [
    path('login/', auth_views.LoginView.as_view(template_name='accounts/login.html'), name='login'),
    path('', IndexView.as_view(), name='home'),  # Página protegida con LoginRequiredMixin
    path('dashboard/', DashboardView.as_view(), name='dashboard'),
    path('help/', help_view, name='help'),
    path('camera/', include('core_apps.camera.urls')),
    path('informes/', include('core_apps.informes.urls')),
    path('admin/', admin.site.urls),
    path("settings/", settings_view, name="settings"),
    path("settings/update-profile/", settings_update_profile, name="settings_update_profile"),
    path("settings/update-email/", settings_update_email, name="settings_update_email"),
    path("settings/update-password/", settings_update_password, name="settings_update_password"),
    path("settings/create-user/", settings_create_user, name="settings_create_user"),
    path('logout/', auth_views.LogoutView.as_view(next_page='login'), name='logout'),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

