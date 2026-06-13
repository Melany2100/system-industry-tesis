import logging
import mimetypes
from email.mime.image import MIMEImage
from html import escape
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.storage import default_storage
from django.core.mail import EmailMultiAlternatives
from django.db.models import Q
from django.utils import timezone


logger = logging.getLogger(__name__)

INCIDENT_EMAIL_SUBJECT = "INCIDENTE LABORAL REGISTRADO"
ADMIN_GROUP_NAMES = ("Admin", "Admins", "Administrador", "Administradores")
CONSOLE_EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"


def get_admin_email_addresses():
    User = get_user_model()
    return list(
        User.objects.filter(
            Q(is_superuser=True)
            | Q(is_staff=True)
            | Q(groups__name__in=ADMIN_GROUP_NAMES)
        )
        .exclude(email__isnull=True)
        .exclude(email__exact="")
        .values_list("email", flat=True)
        .distinct()
    )


def _read_evidence(image_path):
    if not image_path:
        return None

    try:
        with default_storage.open(image_path, "rb") as evidence_file:
            content = evidence_file.read()
    except Exception:
        logger.exception("No se pudo leer la evidencia del incidente %s", image_path)
        return None

    filename = Path(image_path).name
    content_type = mimetypes.guess_type(filename)[0] or "image/jpeg"
    return filename, content_type, content


def send_incident_email(event):
    person = event.authorized_person
    recipient = (person.correo or "").strip() if person else ""

    if not recipient:
        logger.info("El incidente %s no tiene una persona con correo asociado", event.pk)
        return False

    admin_emails = [
        email for email in get_admin_email_addresses()
        if email.lower() != recipient.lower()
    ]
    event_name = event.get_event_type_display()
    person_name = person.get_full_name() or "Usuario"
    event_time = timezone.localtime(event.timestamp).strftime("%d/%m/%Y %H:%M:%S")
    camera_name = event.camera.nombre if event.camera else "No especificada"
    evidence = _read_evidence(event.image_path)
    evidence_text = "La evidencia fotografica se encuentra adjunta."

    if evidence is None:
        evidence_text = "Este incidente no dispone de evidencia fotografica."

    plain_body = (
        "ESTIMADO USUARIO, SE HA REGISTRADO UN INCIDENTE LABORAL.\n\n"
        f"Persona identificada: {person_name}\n"
        f"Incidente incumplido: {event_name}\n"
        f"Detalle: {event.details}\n"
        f"Severidad: {event.get_severity_display()}\n"
        f"Camara: {camera_name}\n"
        f"Fecha y hora: {event_time}\n\n"
        f"{evidence_text}"
    )
    evidence_html = "<p>Este incidente no dispone de evidencia fotografica.</p>"

    if evidence is not None:
        evidence_html = (
            '<p><strong>Evidencia:</strong></p>'
            '<p><img src="cid:incident-evidence" alt="Evidencia del incidente" '
            'style="max-width: 100%; height: auto;"></p>'
        )

    html_body = f"""
        <p>ESTIMADO USUARIO, SE HA REGISTRADO UN INCIDENTE LABORAL.</p>
        <p><strong>Persona identificada:</strong> {escape(person_name)}</p>
        <p><strong>Incidente incumplido:</strong> {escape(event_name)}</p>
        <p><strong>Detalle:</strong> {escape(event.details)}</p>
        <p><strong>Severidad:</strong> {escape(event.get_severity_display())}</p>
        <p><strong>Camara:</strong> {escape(camera_name)}</p>
        <p><strong>Fecha y hora:</strong> {event_time}</p>
        {evidence_html}
    """

    message = EmailMultiAlternatives(
        subject=INCIDENT_EMAIL_SUBJECT,
        body=plain_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[recipient],
        cc=admin_emails,
    )
    message.attach_alternative(html_body, "text/html")

    if evidence is not None:
        filename, content_type, content = evidence
        image = MIMEImage(content, _subtype=content_type.split("/", 1)[-1])
        image.add_header("Content-ID", "<incident-evidence>")
        image.add_header("Content-Disposition", "inline", filename=filename)
        message.attach(image)

    message.send(fail_silently=False)
    return True


def notify_incident_by_email(event_id):
    from core_apps.camera.models import SecurityEvent

    try:
        event = SecurityEvent.objects.select_related(
            "authorized_person", "camera"
        ).get(pk=event_id)

        person = event.authorized_person
        recipient = (person.correo or "").strip() if person else ""

        if not recipient:
            event.email_status = "SKIPPED"
            event.email_error = "La persona identificada no tiene correo registrado."
            event.save(update_fields=["email_status", "email_error"])
            return False

        admin_emails = [
            email for email in get_admin_email_addresses()
            if email.lower() != recipient.lower()
        ]
        event.email_recipient = recipient
        event.email_cc = ", ".join(admin_emails)

        if settings.EMAIL_BACKEND == CONSOLE_EMAIL_BACKEND:
            event.email_status = "FAILED"
            event.email_error = (
                "El backend de correo esta en modo consola. Configure EMAIL_BACKEND "
                "con SMTP y agregue las credenciales en el archivo .env."
            )
            event.save(update_fields=[
                "email_status", "email_recipient", "email_cc", "email_error"
            ])
            return False

        sent = send_incident_email(event)

        if not sent:
            raise RuntimeError("El servidor de correo no confirmo el envio.")

        event.email_status = "SENT"
        event.email_sent_at = timezone.now()
        event.email_error = ""
        event.save(update_fields=[
            "email_status",
            "email_recipient",
            "email_cc",
            "email_sent_at",
            "email_error",
        ])
        return True
    except Exception as exc:
        logger.exception("No se pudo enviar el correo del incidente %s", event_id)

        try:
            SecurityEvent.objects.filter(pk=event_id).update(
                email_status="FAILED",
                email_error=str(exc)[:2000],
            )
        except Exception:
            logger.exception("No se pudo guardar el error de correo del incidente %s", event_id)

        return False
