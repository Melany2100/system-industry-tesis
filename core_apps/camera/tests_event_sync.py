from unittest.mock import Mock, patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core_apps.camera.models import Camera, EventSyncOutbox, SecurityEvent
from core_apps.camera.services.event_sync import (
    enqueue_security_event,
    process_due_events,
)
from core_apps.informes.models import Informe


@override_settings(
    SMRI_NODE_ROLE="cloud",
    SMRI_EVENT_SYNC_TOKEN="shared-test-token",
)
class IngestEdgeEventTests(TestCase):
    def setUp(self):
        self.camera = Camera.objects.create(
            nombre="Cámara Principal",
            source="push://camera-principal",
            stream_path="camera-principal",
        )
        self.payload = {
            "source_event_key": "planta-principal:42",
            "event_type": "phone_usage",
            "severity": "MEDIO",
            "details": "Uso de celular detectado",
            "timestamp": timezone.now().isoformat(),
            "camera_stream_path": "camera-principal",
            "camera_name": "Cámara Principal",
            "should_alert": "1",
            "epp_correcto": "0",
        }

    def _post(self, token="shared-test-token"):
        return self.client.post(
            reverse("ingest_edge_event"),
            data=self.payload,
            HTTP_AUTHORIZATION=f"Bearer {token}",
            HTTP_IDEMPOTENCY_KEY="planta-principal:42",
        )

    def test_requires_shared_bearer_token(self):
        response = self._post(token="wrong")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(SecurityEvent.objects.count(), 0)

    def test_creates_event_report_and_is_idempotent(self):
        first = self._post()
        second = self._post()

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.json()["duplicate"])
        self.assertEqual(SecurityEvent.objects.count(), 1)
        self.assertEqual(Informe.objects.count(), 1)
        event = SecurityEvent.objects.get()
        self.assertEqual(event.source_event_key, "planta-principal:42")
        self.assertEqual(event.camera, self.camera)
        self.assertEqual(event.email_status, "SKIPPED")


@override_settings(
    SMRI_NODE_ROLE="edge",
    SMRI_EDGE_ENABLED=True,
    SMRI_EDGE_NODE_ID="planta-principal",
    SMRI_EVENT_SYNC_URL="https://app.example.test/camera/api/v1/events/",
    SMRI_EVENT_SYNC_TOKEN="shared-test-token",
    SMRI_EVENT_SYNC_TIMEOUT_SECONDS=5,
    SMRI_EVENT_SYNC_BATCH_SIZE=10,
)
class EventOutboxTests(TestCase):
    def test_successful_delivery_marks_outbox_synced(self):
        event = SecurityEvent.objects.create(
            event_type="phone_usage",
            severity="MEDIO",
            details="Uso de celular detectado",
        )
        outbox = enqueue_security_event(event.pk)
        EventSyncOutbox.objects.filter(pk=outbox.pk).update(
            next_attempt_at=timezone.now()
        )
        response = Mock(status_code=201, text='{"success": true}')

        with patch(
            "core_apps.camera.services.event_sync.requests.post",
            return_value=response,
        ) as mocked_post:
            result = process_due_events()

        outbox.refresh_from_db()
        self.assertEqual(result, {"synced": 1, "failed": 0})
        self.assertEqual(outbox.status, EventSyncOutbox.STATUS_SYNCED)
        headers = mocked_post.call_args.kwargs["headers"]
        self.assertEqual(headers["Authorization"], "Bearer shared-test-token")
        self.assertEqual(headers["Idempotency-Key"], "planta-principal:1")
