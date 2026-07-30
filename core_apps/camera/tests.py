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
from core_apps.camera.services.risk_yolo_detector import RiskYoloDetector
from core_apps.camera.models import AuthorizedPerson, Camera, SecurityEvent
from core_apps.camera.views import (
    _clean_live_log_message,
    _get_missing_ppe_items,
    _get_supported_required_ppe_items,
    _is_ppe_person_corroborated,
    _is_valid_ppe_person,
    _live_log_kind,
    _rtsp_stream_sources,
    _scale_box_between_frames,
)
from core_apps.camera.utils import (
    PPE_EMAIL_DEFERRED_REASON,
    create_security_event,
    format_security_event_details,
)


class LiveLogPresentationTests(SimpleTestCase):
    def test_translates_technical_labels_and_removes_broken_symbols(self):
        message = _clean_live_log_message(
            "âš  PPE [Camara 1]: Falta EPP: mask | bottle"
        )

        self.assertNotIn("â", message)
        self.assertNotIn("PPE", message)
        self.assertIn("EPP", message)
        self.assertIn("mascarilla", message)
        self.assertIn("botella", message)

    def test_assigns_requested_colors_by_event_meaning(self):
        self.assertEqual(_live_log_kind("Falta EPP: mascarilla"), "danger")
        self.assertEqual(_live_log_kind("Objeto no autorizado: mochila"), "danger")
        self.assertEqual(_live_log_kind("Autorizado: Ana Perez"), "identity")
        self.assertEqual(_live_log_kind("EPP OK"), "success")


class EventDetailPresentationTests(SimpleTestCase):
    def test_ppe_event_uses_concise_non_repetitive_fields(self):
        details = format_security_event_details(
            "ppe_missing",
            "Falta EPP: mask | Nivel: ALTO | Persona: Persona no identificada | Estado: No autorizado",
        )

        self.assertIn("Motivo: Falta de EPP", details)
        self.assertIn("Descripcion: Falta mascarilla.", details)
        self.assertIn("Categoria: Falta de EPP", details)
        self.assertIn("Persona: Desconocido/a", details)
        self.assertIn("Estado: No autorizado", details)

    def test_existing_ppe_event_is_normalized_when_displayed(self):
        details = format_security_event_details(
            "ppe_missing",
            "Motivo: Falta EPP\nDescripcion: Falta EPP: mask ALTO EVA VARGAS\nCategoria: Falta de EPP\nPersona: EVA VARGAS",
        )

        self.assertIn("Motivo: Falta de EPP", details)
        self.assertIn("Descripcion: Falta mascarilla.", details)
        self.assertNotIn("mask", details)

    def test_describes_multiple_missing_ppe_items_in_spanish(self):
        details = format_security_event_details(
            "ppe_missing",
            "Falta EPP: mask, gloves, earmuffs, hardhat, safety glasses | Nivel: ALTO",
        )

        self.assertIn(
            "Descripcion: Faltan mascarilla, guantes, protectores auditivos, casco, gafas de proteccion.",
            details,
        )

    def test_preserves_missing_items_when_event_is_formatted_twice(self):
        first_format = format_security_event_details(
            "ppe_missing",
            "Falta EPP: gloves, earmuffs, safety glasses | Persona: EVA VARGAS | Estado: Autorizado",
        )
        second_format = format_security_event_details(
            "ppe_missing",
            first_format,
        )

        self.assertIn(
            "Descripcion: Faltan guantes, protectores auditivos, gafas de proteccion.",
            second_format,
        )
        self.assertNotIn("Se detectó EPP incompleto", second_format)

    def test_additional_animal_event_does_not_replace_ppe_description(self):
        details = format_security_event_details(
            "ppe_missing",
            "Falta EPP: mask | Persona: Persona no identificada | Evento adicional: Acceso no autorizado: gato en el area monitoreada",
        )

        self.assertIn("Descripcion: Falta mascarilla.", details)
        self.assertIn(
            "Evento adicional: Se detectó un gato en el área monitoreada.",
            details,
        )


@override_settings(
    PPE_PERSON_CONFIDENCE=0.55,
    PPE_PERSON_MIN_AREA_RATIO=0.06,
    PPE_PERSON_MIN_HEIGHT_RATIO=0.30,
    PPE_PERSON_MIN_ASPECT_RATIO=0.75,
)
class PpePersonValidationTests(SimpleTestCase):
    def test_only_requires_items_supported_by_model(self):
        names = {
            0: "Hardhat",
            1: "Mask",
            2: "NO-Hardhat",
            3: "NO-Mask",
            4: "Person",
        }

        self.assertEqual(
            _get_supported_required_ppe_items(names),
            ("mask", "hardhat"),
        )

    def test_supports_all_required_items_from_sh17_model(self):
        names = {
            0: "person",
            1: "ear-mufs",
            2: "face-mask",
            3: "glasses",
            4: "gloves",
            5: "helmet",
        }

        self.assertEqual(
            _get_supported_required_ppe_items(names),
            ("mask", "gloves", "earmuffs", "hardhat", "safety glasses"),
        )

    def test_returns_only_items_missing_from_one_person(self):
        required = ("mask", "gloves", "earmuffs", "hardhat", "safety glasses")
        present = {"face-mask", "helmet", "gloves"}

        self.assertEqual(
            _get_missing_ppe_items(present, required),
            ("earmuffs", "safety glasses"),
        )

    def test_returns_no_missing_items_when_person_has_all_ppe(self):
        required = ("mask", "gloves", "earmuffs", "hardhat", "safety glasses")
        present = {"face-mask", "gloves", "ear-mufs", "helmet", "glasses"}

        self.assertEqual(_get_missing_ppe_items(present, required), ())

    def test_rejects_low_confidence_false_person(self):
        self.assertFalse(
            _is_valid_ppe_person((480, 640, 3), (100, 80, 300, 420), 0.40)
        )

    def test_rejects_small_background_detection(self):
        self.assertFalse(
            _is_valid_ppe_person((480, 640, 3), (300, 220, 360, 330), 0.90)
        )

    def test_rejects_wide_furniture_shaped_detection(self):
        self.assertFalse(
            _is_valid_ppe_person((480, 640, 3), (40, 180, 500, 390), 0.90)
        )

    def test_accepts_plausible_person_detection(self):
        self.assertTrue(
            _is_valid_ppe_person((480, 640, 3), (180, 70, 360, 450), 0.85)
        )

    def test_rejects_ppe_box_without_second_human_detection(self):
        detector = SimpleNamespace(
            last_person_boxes=[],
            last_person_boxes_at=99.0,
        )

        self.assertFalse(
            _is_ppe_person_corroborated(
                (180, 70, 360, 450), detector, [], now=100.0
            )
        )

    def test_accepts_overlapping_second_person_detection(self):
        detector = SimpleNamespace(
            last_person_boxes=[(170, 60, 370, 455, 0.90)],
            last_person_boxes_at=99.0,
        )

        self.assertTrue(
            _is_ppe_person_corroborated(
                (180, 70, 360, 450), detector, [], now=100.0
            )
        )

    def test_accepts_recent_face_inside_ppe_person_box(self):
        faces = [{"coords": (220, 90, 290, 170), "last_seen": 99.0}]

        self.assertTrue(
            _is_ppe_person_corroborated(
                (180, 70, 360, 450), None, faces, now=100.0
            )
        )


@override_settings(
    ANIMAL_PERSON_OVERLAP_RATIO=0.35,
    ANIMAL_CONFIRMATION_FRAMES=2,
)
class AnimalFalsePositiveFilterTests(SimpleTestCase):
    def test_rejects_animal_box_mostly_inside_person(self):
        self.assertTrue(
            RiskYoloDetector._animal_overlaps_person(
                (300, 120, 390, 260),
                [(250, 40, 430, 460)],
            )
        )

    def test_requires_two_consecutive_animal_detections(self):
        detector = RiskYoloDetector.__new__(RiskYoloDetector)
        detector._animal_memory = {}
        detector._scan_id = 1

        self.assertFalse(detector._confirm_animal("cat", (40, 200, 180, 400)))
        detector._scan_id = 2
        self.assertTrue(detector._confirm_animal("cat", (45, 205, 185, 405)))


@override_settings(
    RTSP_DUAL_STREAM_ENABLED=True,
    RTSP_MAIN_STREAM_NAME="stream1",
    RTSP_SUB_STREAM_NAME="stream2",
)
class HighResolutionCameraConfigurationTests(SimpleTestCase):
    def test_vigi_main_url_generates_preview_and_analysis_streams(self):
        preview, analysis = _rtsp_stream_sources(
            "rtsp://usuario:clave@192.168.1.60:554/stream1"
        )

        self.assertEqual(
            preview,
            "rtsp://usuario:clave@192.168.1.60:554/stream2",
        )
        self.assertEqual(
            analysis,
            "rtsp://usuario:clave@192.168.1.60:554/stream1",
        )

    def test_vigi_substream_url_also_enables_main_analysis_stream(self):
        preview, analysis = _rtsp_stream_sources(
            "rtsp://usuario:clave@192.168.1.60:554/stream2"
        )

        self.assertTrue(preview.endswith("/stream2"))
        self.assertTrue(analysis.endswith("/stream1"))

    def test_non_vigi_rtsp_source_keeps_single_stream(self):
        source = "rtsp://192.168.1.70/live/ch00"

        self.assertEqual(_rtsp_stream_sources(source), (source, None))

    def test_scales_high_resolution_box_to_live_frame(self):
        scaled = _scale_box_between_frames(
            (300, 150, 900, 750),
            (1080, 1920, 3),
            (360, 640, 3),
        )

        self.assertEqual(scaled, (100, 50, 300, 250))


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

    @override_settings(PPE_EMAIL_EVERY_N_EVENTS=3)
    @patch("core_apps.camera.utils.close_old_connections")
    @patch("core_apps.camera.utils._queue_incident_email")
    def test_ppe_email_is_queued_only_on_third_registered_event(
        self,
        queue_email,
        close_connections,
    ):
        created_events = []

        with self.captureOnCommitCallbacks(execute=True):
            for _ in range(3):
                created_events.append(
                    create_security_event(
                        event_type="ppe_missing",
                        details="Falta EPP: gloves",
                        camera=self.camera,
                        authorized_person=self.person,
                        epp_correcto=False,
                        severity="ALTO",
                    )
                )

        for event in created_events:
            event.refresh_from_db()

        self.assertEqual(created_events[0].email_status, "SKIPPED")
        self.assertEqual(created_events[0].email_error, PPE_EMAIL_DEFERRED_REASON)
        self.assertEqual(created_events[1].email_status, "SKIPPED")
        self.assertEqual(created_events[2].email_status, "PENDING")
        queue_email.assert_called_once_with(created_events[2].pk)

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
