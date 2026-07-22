from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core import mail
from django.contrib.auth.models import Group, User
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core_apps.camera.services.incident_email import (
    INCIDENT_EMAIL_SUBJECT,
    notify_incident_by_email,
    send_incident_email,
)
from core_apps.camera.models import AuthorizedPerson, Camera, SecurityEvent


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="alertas@example.com",
)
class IncidentEmailTests(SimpleTestCase):
    def setUp(self):
        self.person = SimpleNamespace(
            correo="trabajador@example.com",
            get_full_name=lambda: "Ana Perez",
        )
        self.event = SimpleNamespace(
            pk=42,
            authorized_person=self.person,
            event_type="ppe_missing",
            details="No utiliza casco de seguridad",
            severity="ALTO",
            timestamp=timezone.make_aware(datetime(2026, 6, 13, 10, 30)),
            camera=SimpleNamespace(nombre="Camara Bodega"),
            image_path=None,
            get_event_type_display=lambda: "Falta de EPP",
            get_severity_display=lambda: "Alto",
        )

    @patch(
        "core_apps.camera.services.incident_email.get_admin_email_addresses",
        return_value=["admin@example.com", "trabajador@example.com"],
    )
    def test_sends_incident_to_person_with_admin_copy(self, admin_emails):
        sent = send_incident_email(self.event)

        self.assertTrue(sent)
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.subject, INCIDENT_EMAIL_SUBJECT)
        self.assertEqual(message.to, ["trabajador@example.com"])
        self.assertEqual(message.cc, ["admin@example.com"])
        self.assertIn(
            "ESTIMADO USUARIO Ana Perez, SE HA REGISTRADO UN INCIDENTE LABORAL",
            message.body,
        )
        self.assertIn("Incidente incumplido: Falta de EPP", message.body)
        self.assertIn("No utiliza casco de seguridad", message.body)
        self.assertIn("no dispone de evidencia", message.body)

    @patch(
        "core_apps.camera.services.incident_email.get_admin_email_addresses",
        return_value=[],
    )
    @patch("core_apps.camera.services.incident_email.default_storage.open")
    def test_embeds_the_evidence_image(self, storage_open, admin_emails):
        image_file = Mock()
        image_file.__enter__ = Mock(return_value=image_file)
        image_file.__exit__ = Mock(return_value=False)
        image_file.read.return_value = b"fake-jpeg-content"
        storage_open.return_value = image_file
        self.event.image_path = "security_events/evidencia.jpg"

        sent = send_incident_email(self.event)

        self.assertTrue(sent)
        message = mail.outbox[0]
        self.assertEqual(len(message.attachments), 1)
        self.assertEqual(message.attachments[0]["Content-ID"], "<incident-evidence>")
        self.assertIn("cid:incident-evidence", message.alternatives[0][0])

    @patch("core_apps.camera.services.incident_email.get_admin_email_addresses")
    def test_does_not_send_without_person_email(self, admin_emails):
        self.person.correo = ""

        sent = send_incident_email(self.event)

        self.assertFalse(sent)
        self.assertEqual(mail.outbox, [])
        admin_emails.assert_not_called()

    @patch(
        "core_apps.camera.services.incident_email.EmailMultiAlternatives.send",
        return_value=0,
    )
    @patch(
        "core_apps.camera.services.incident_email.get_admin_email_addresses",
        return_value=[],
    )
    def test_reports_failure_when_backend_sends_zero_messages(self, admin_emails, send):
        sent = send_incident_email(self.event)

        self.assertFalse(sent)

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend"
    )
    @patch(
        "core_apps.camera.services.incident_email.get_admin_email_addresses",
        return_value=["admin@example.com"],
    )
    @patch("core_apps.camera.models.SecurityEvent.objects")
    def test_console_backend_is_recorded_as_failed(self, objects, admin_emails):
        event = Mock()
        event.authorized_person = self.person
        objects.select_related.return_value.get.return_value = event

        sent = notify_incident_by_email(42)

        self.assertFalse(sent)
        self.assertEqual(event.email_status, "FAILED")
        self.assertIn("modo consola", event.email_error)
        event.save.assert_called_once()


class SecurityEventFunctionalTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        admin_group = Group.objects.get(name="Administrador")
        operator_group = Group.objects.get(name="Operador")
        cls.admin = User.objects.create_user("admin_eventos", password="Clave-2026", is_superuser=True)
        cls.admin.groups.add(admin_group)
        cls.operator = User.objects.create_user(
            "maria", first_name="Maria", last_name="Lopez",
            email="maria@smri.test", password="Clave-2026",
        )
        cls.operator.groups.add(operator_group)
        cls.person = AuthorizedPerson.objects.create(
            nombres="Maria", apellidos="Lopez", correo="maria@smri.test",
            cargo="Operadora", face_encoding="[0.2, 0.3]",
        )
        cls.camera = Camera.objects.create(nombre="Camara Planta", source="0")
        cls.own_event = SecurityEvent.objects.create(
            event_type="phone_usage", severity="MEDIO", details="Uso prolongado de celular",
            authorized_person=cls.person, camera=cls.camera,
        )
        cls.other_event = SecurityEvent.objects.create(
            event_type="dangerous_object", severity="ALTO", details="Tijeras detectadas",
            camera=cls.camera,
        )

    def test_admin_lists_all_events_with_daily_summary(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("get_security_events"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload["events"]), 2)
        self.assertEqual(payload["summary"]["total_today"], 2)
        self.assertEqual(payload["summary"]["high_priority_today"], 1)

    def test_operator_only_lists_events_associated_with_own_person(self):
        self.client.force_login(self.operator)
        response = self.client.get(reverse("get_security_events"))

        self.assertEqual(response.status_code, 200)
        event_ids = [item["id"] for item in response.json()["events"]]
        self.assertEqual(event_ids, [self.own_event.id])

    def test_operator_can_review_own_event_but_not_unrelated_event(self):
        self.client.force_login(self.operator)
        own_response = self.client.post(reverse("review_security_event", args=[self.own_event.id]))
        other_response = self.client.post(reverse("review_security_event", args=[self.other_event.id]))

        self.assertEqual(own_response.status_code, 200)
        self.assertEqual(other_response.status_code, 403)
        self.own_event.refresh_from_db()
        self.assertEqual(self.own_event.reviewed_by, self.operator)
        self.assertIsNotNone(self.own_event.reviewed_at)

    def test_admin_resolves_event_and_records_manager(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("mark_event_resolved", args=[self.other_event.id]))

        self.assertEqual(response.status_code, 200)
        self.other_event.refresh_from_db()
        self.assertTrue(self.other_event.resolved)
        self.assertEqual(self.other_event.managed_by, self.admin)
        self.assertIsNotNone(self.other_event.managed_at)

    def test_operator_cannot_resolve_event(self):
        self.client.force_login(self.operator)
        response = self.client.post(reverse("mark_event_resolved", args=[self.own_event.id]))

        self.assertEqual(response.status_code, 403)
        self.own_event.refresh_from_db()
        self.assertFalse(self.own_event.resolved)
