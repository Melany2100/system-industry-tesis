import json

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from core_apps.camera.models import AuthorizedPerson, Camera, SecurityEvent
from core_apps.common.models import UserSetting
from core_apps.informes.models import Informe


class FunctionalDataMixin:
    password = "Clave-Segura-2026"

    @classmethod
    def setUpTestData(cls):
        cls.admin_group = Group.objects.get(name="Administrador")
        cls.jefe_group = Group.objects.get(name="Jefe")
        cls.operator_group = Group.objects.get(name="Operador")

        cls.admin = User.objects.create_user(
            "admin_demo", email="admin@smri.test", password=cls.password,
            is_staff=True, is_superuser=True,
        )
        cls.admin.groups.add(cls.admin_group)
        cls.jefe = User.objects.create_user(
            "jefe_demo", email="jefe@smri.test", password=cls.password,
        )
        cls.jefe.groups.add(cls.jefe_group)
        cls.operator = User.objects.create_user(
            "ana", first_name="Ana", last_name="Perez",
            email="ana@smri.test", password=cls.password,
        )
        cls.operator.groups.add(cls.operator_group)

        cls.person = AuthorizedPerson.objects.create(
            nombres="Ana", apellidos="Perez", correo="ana@smri.test",
            cargo="Operadora", face_encoding="[0.1, 0.2]",
            registered_by=cls.admin,
        )
        cls.camera = Camera.objects.create(
            nombre="Camara Bodega", source="rtsp://127.0.0.1/demo",
            ubicacion="Bodega", is_active=False,
        )
        cls.own_event = SecurityEvent.objects.create(
            event_type="ppe_missing", severity="ALTO",
            details="Ana Perez no utiliza casco de seguridad",
            authorized_person=cls.person, camera=cls.camera,
        )
        cls.other_event = SecurityEvent.objects.create(
            event_type="face_unknown", severity="MEDIO",
            details="Persona desconocida en acceso principal", camera=cls.camera,
        )
        Informe.objects.create(
            camara=cls.camera.nombre, persona_detectada="Ana Perez",
            epp_correcto=False, descripcion="Falta de EPP",
            security_event=cls.own_event,
        )

    def post_json(self, name, payload):
        return self.client.post(
            reverse(name), data=json.dumps(payload), content_type="application/json"
        )


class AuthenticationAndNavigationFunctionalTests(FunctionalDataMixin, TestCase):
    def test_private_pages_redirect_anonymous_user_to_login(self):
        for name in ("home", "dashboard", "settings", "alerta", "lista_informes"):
            with self.subTest(route=name):
                response = self.client.get(reverse(name), follow=True)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.resolver_match.url_name, "login")

    def test_valid_login_opens_admin_home_and_activates_cameras(self):
        self.assertTrue(self.client.login(username=self.admin.username, password=self.password))
        response = self.client.get(reverse("home"))

        self.camera.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "home/index.html")
        self.assertTrue(self.camera.is_active)

    def test_operator_is_redirected_from_management_home_to_dashboard(self):
        self.client.force_login(self.operator)
        response = self.client.get(reverse("home"))

        self.assertRedirects(response, reverse("dashboard"), fetch_redirect_response=False)


class UserManagementFunctionalTests(FunctionalDataMixin, TestCase):
    def test_admin_creates_operator_with_settings_and_role(self):
        self.client.force_login(self.admin)
        response = self.post_json("settings_create_user", {
            "username": "nuevo_operador", "first_name": "Luis",
            "last_name": "Mora", "email": "luis@smri.test",
            "password": "Otra-Clave-2026", "role": "operador",
        })

        self.assertEqual(response.status_code, 200)
        created = User.objects.get(username="nuevo_operador")
        self.assertTrue(created.check_password("Otra-Clave-2026"))
        self.assertTrue(created.groups.filter(name="Operador").exists())
        self.assertTrue(UserSetting.objects.filter(user=created).exists())

    def test_operator_cannot_create_users(self):
        self.client.force_login(self.operator)
        response = self.post_json("settings_create_user", {
            "username": "sin_permiso", "password": "Otra-Clave-2026",
            "role": "operador",
        })

        self.assertEqual(response.status_code, 403)
        self.assertFalse(User.objects.filter(username="sin_permiso").exists())

    def test_jefe_cannot_create_an_admin(self):
        self.client.force_login(self.jefe)
        response = self.post_json("settings_create_user", {
            "username": "admin_ilegal", "password": "Otra-Clave-2026",
            "role": "admin",
        })

        self.assertEqual(response.status_code, 403)
        self.assertFalse(User.objects.filter(username="admin_ilegal").exists())

    def test_user_updates_profile_email_and_password(self):
        self.client.force_login(self.operator)
        profile = self.post_json("settings_update_profile", {
            "username": "ana.actualizada", "first_name": "Ana Maria", "last_name": "Perez",
        })
        email = self.post_json("settings_update_email", {"new_email": "ana.nueva@smri.test"})
        password = self.post_json("settings_update_password", {
            "current_password": self.password,
            "new_password": "Nueva-Clave-2026", "confirm_password": "Nueva-Clave-2026",
        })

        self.assertEqual((profile.status_code, email.status_code, password.status_code), (200, 200, 200))
        self.operator.refresh_from_db()
        self.assertEqual(self.operator.username, "ana.actualizada")
        self.assertEqual(self.operator.email, "ana.nueva@smri.test")
        self.assertTrue(self.operator.check_password("Nueva-Clave-2026"))
        self.assertEqual(self.client.get(reverse("settings")).status_code, 200)


class DashboardFunctionalTests(FunctionalDataMixin, TestCase):
    def test_admin_dashboard_aggregates_all_demo_data(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total_events"], 2)
        self.assertEqual(len(response.context["recent_reports"]), 1)
        self.assertEqual(response.context["pending_events"], 2)

    def test_operator_dashboard_only_contains_own_identity_data(self):
        self.client.force_login(self.operator)
        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total_events"], 1)
        self.assertEqual(len(response.context["recent_reports"]), 1)
        self.assertEqual(list(response.context["recent_events"]), [self.own_event])


class ReportFunctionalTests(FunctionalDataMixin, TestCase):
    def test_only_management_roles_can_view_reports(self):
        self.client.force_login(self.operator)
        self.assertEqual(self.client.get(reverse("lista_informes")).status_code, 403)

        self.client.force_login(self.jefe)
        response = self.client.get(reverse("lista_informes"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total_count"], 1)
        self.assertEqual(response.context["critical_count"], 1)

    def test_admin_exports_demo_report_in_supported_formats(self):
        self.client.force_login(self.admin)
        expected_types = {
            "csv": "text/csv; charset=utf-8",
            "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "pdf": "application/pdf",
        }

        for extension, content_type in expected_types.items():
            with self.subTest(format=extension):
                response = self.client.get(reverse("exportar_informes", args=["mensual", extension]))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response["Content-Type"], content_type)
                self.assertIn(f".{extension}", response["Content-Disposition"])
                self.assertGreater(len(response.content), 50)

    def test_invalid_report_parameters_are_rejected(self):
        self.client.force_login(self.admin)
        invalid_format = self.client.get(reverse("exportar_informes", args=["mensual", "json"]))
        invalid_period = self.client.get(reverse("exportar_informes", args=["anual", "csv"]))

        self.assertEqual(invalid_format.status_code, 400)
        self.assertEqual(invalid_period.status_code, 400)
