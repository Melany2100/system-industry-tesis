from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from core_apps.camera.models import SecurityEvent

User = get_user_model()


class CameraModuleTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username="tester",
            email="tester@example.com",
            password="testpass123",
        )

    def test_camera_view_requires_login(self):
        response = self.client.get(reverse("camera"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response.url)

    def test_camera_view_authenticated_returns_200(self):
        self.client.login(username="tester", password="testpass123")
        response = self.client.get(reverse("camera"))
        self.assertEqual(response.status_code, 200)

    def test_live_status_authenticated_returns_json(self):
        self.client.login(username="tester", password="testpass123")
        response = self.client.get(reverse("live_status"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("lines", response.json())
        self.assertIn("last_id", response.json())

    @patch("core_apps.camera.views.probe_camera")
    def test_camera_status_ok(self, mock_probe_camera):
        self.client.login(username="tester", password="testpass123")

        class DummyCap:
            def release(self):
                return None

        mock_probe_camera.return_value = (DummyCap(), None)

        response = self.client.get(reverse("camera_status"))
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["success"], True)

    @patch("core_apps.camera.views.probe_camera")
    def test_camera_status_without_camera(self, mock_probe_camera):
        self.client.login(username="tester", password="testpass123")
        mock_probe_camera.return_value = (None, "No se encontró una cámara disponible.")

        response = self.client.get(reverse("camera_status"))
        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(body["success"], False)
        self.assertIn("message", body)

    def test_get_security_events_returns_list(self):
        self.client.login(username="tester", password="testpass123")

        SecurityEvent.objects.create(
            event_type="dangerous_object",
            details="Prueba de evento",
            related_user=self.user,
        )

        response = self.client.get(reverse("get_security_events"))
        self.assertEqual(response.status_code, 200)

        body = response.json()
        self.assertIn("events", body)
        self.assertEqual(len(body["events"]), 1)
        self.assertEqual(body["events"][0]["event_type"], "dangerous_object")

    def test_get_events_returns_list(self):
        self.client.login(username="tester", password="testpass123")

        SecurityEvent.objects.create(
            event_type="face_unknown",
            details="Persona no reconocida",
            related_user=self.user,
        )

        response = self.client.get(reverse("get_events"))
        self.assertEqual(response.status_code, 200)

        body = response.json()
        self.assertIn("events", body)
        self.assertEqual(len(body["events"]), 1)
        self.assertEqual(body["events"][0]["event_type"], "face_unknown")

    def test_mark_event_resolved_requires_post(self):
        self.client.login(username="tester", password="testpass123")

        event = SecurityEvent.objects.create(
            event_type="dangerous_object",
            details="Evento para resolver",
            related_user=self.user,
        )

        response = self.client.get(
            reverse("mark_event_resolved", kwargs={"event_id": event.id})
        )
        self.assertEqual(response.status_code, 405)

    def test_mark_event_resolved_post_updates_event(self):
        self.client.login(username="tester", password="testpass123")

        event = SecurityEvent.objects.create(
            event_type="dangerous_object",
            details="Evento para resolver",
            related_user=self.user,
            resolved=False,
        )

        response = self.client.post(
            reverse("mark_event_resolved", kwargs={"event_id": event.id})
        )
        self.assertEqual(response.status_code, 200)

        event.refresh_from_db()
        self.assertTrue(event.resolved)

    def test_register_face_without_file_returns_400(self):
        self.client.login(username="tester", password="testpass123")
        response = self.client.post(reverse("register_face"), {})
        self.assertEqual(response.status_code, 400)