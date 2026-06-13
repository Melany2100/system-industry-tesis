from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core import mail
from django.test import SimpleTestCase, override_settings
from django.utils import timezone

from core_apps.camera.services.incident_email import (
    INCIDENT_EMAIL_SUBJECT,
    notify_incident_by_email,
    send_incident_email,
)


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
            "ESTIMADO USUARIO, SE HA REGISTRADO UN INCIDENTE LABORAL",
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
