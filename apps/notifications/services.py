"""Logique de notification : mise en file, envoi, rappels de rendez-vous.

Les règles vivent ici (fonctions module-level), comme dans
``apps.appointments.services`` : les vues web, l'API et les tâches Celery
passent toutes par elles.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .models import Notification

logger = logging.getLogger(__name__)

#: Rappels planifiés à la prise de rendez-vous (canaux patient).
APPOINTMENT_CHANNELS = ("in_app", "whatsapp", "email")

#: Canaux nommés dans ``APPOINTMENT_REMINDER_SCHEDULE`` (minuscules) → valeurs du modèle.
CHANNEL_ALIASES = {
    "in_app": Notification.Channel.IN_APP,
    "whatsapp": Notification.Channel.WHATSAPP,
    "email": Notification.Channel.EMAIL,
}


def _normalized_channel(name: str) -> str:
    """Convertit ``in_app`` en ``IN_APP`` (canal déjà normalisé : inchangé)."""
    return CHANNEL_ALIASES.get(str(name).lower(), str(name))


# ---------------------------------------------------------------------------
# Mise en file d'attente
# ---------------------------------------------------------------------------


def queue_notification(
    *,
    clinic,
    title: str,
    body: str,
    channel: str,
    category: str = Notification.Category.SYSTEM,
    recipient=None,
    patient=None,
    appointment=None,
    scheduled_for=None,
):
    """Prépare une notification, prête à partir au moment convenu.

    Renvoie la notification créée, ou ``None`` si l'envoi est impossible
    (aucun contact connu sur ce canal) — une adresse absente n'est pas une
    erreur métier, seulement un canal non disponible.

    - canal ``IN_APP`` ou ``EMAIL`` interne : un destinataire du personnel ;
    - canal ``WHATSAPP``/``EMAIL`` patient : un dossier patient.
    """
    recipient_contact = _user_contact(recipient)
    patient_contact = _patient_contact(patient)

    if channel == Notification.Channel.IN_APP:
        if recipient is None:
            return None
        phone, email = recipient_contact["phone"], recipient_contact["email"]
    elif channel == Notification.Channel.WHATSAPP:
        phone = patient_contact["phone"] or recipient_contact["phone"]
        if not phone:
            return None
        email = ""
    elif channel == Notification.Channel.EMAIL:
        email = patient_contact["email"] or recipient_contact["email"]
        if not email:
            return None
        phone = ""
    else:
        raise ValidationError(_("Canal de notification inconnu."))

    notification = Notification(
        clinic=clinic,
        recipient=recipient,
        patient=patient,
        appointment=appointment,
        channel=channel,
        category=category,
        title=(title or "").strip()[:140],
        body=body or "",
        phone=phone,
        email=email,
        scheduled_for=scheduled_for,
    )
    notification.full_clean()
    notification.save()
    return notification


def queue_bulk(notices) -> list:
    """Enregistre plusieurs notifications, ignore les impossibles."""
    return (
        [notices]
        if not isinstance(notices, list)
        else [notice for notice in notices if notice is not None]
    )


def _user_contact(user) -> dict:
    """Coordonnées d'un membre du personnel (peut être ``None``)."""
    if user is None or not getattr(user, "is_authenticated", False):
        return {"phone": "", "email": ""}
    return {
        "phone": (getattr(user, "phone", "") or "").strip(),
        "email": (
            getattr(user, "notification_email", "") or getattr(user, "email", "") or ""
        ).strip(),
    }


def _patient_contact(patient) -> dict:
    """Coordonnées d'un patient (peut être ``None``)."""
    if patient is None:
        return {"phone": "", "email": ""}
    return {
        "phone": (patient.phone or patient.phone_secondary or "").strip(),
        "email": (patient.email or "").strip(),
    }


def unread_count(user) -> int:
    """Badges de la barre de navigation (messages in-app non lus)."""
    if user is None or not getattr(user, "is_authenticated", False):
        return 0
    return Notification.objects.filter(recipient=user, read_at__isnull=True).count()


def due_set():
    """Notifications en attente dont l'heure est venue (consommable et testé)."""
    now = timezone.now()
    return (
        Notification.objects.select_related(
            "clinic", "recipient", "patient", "appointment"
        )
        .filter(status=Notification.Status.PENDING)
        .filter(Q(scheduled_for__isnull=True) | Q(scheduled_for__lte=now))
        .order_by("scheduled_for", "created_at")
    )


# ---------------------------------------------------------------------------
# Envoi
# ---------------------------------------------------------------------------


def dispatch_pending(*, only_clinic=None, limit: int | None = None) -> dict:
    """Envoie toutes les notifications dues (tâche Celery périodique).

    Renvoie un résumé : envois réussis, échecs conservés pour une nouvelle
    tentative, et volume restant dans la file. Chaque message in-app livré
    pousse aussi une notification navigateur (Web Push, best effort).
    """

    queryset = due_set()
    if only_clinic is not None:
        queryset = queryset.filter(clinic=only_clinic)
    if limit:
        queryset = queryset[:limit]
    items = list(queryset)

    sent = failed = pushed = 0
    for notification in items:
        ok, detail = _deliver(notification)
        if ok:
            notification.mark_sent()
            if notification.channel == Notification.Channel.IN_APP:
                pushed += _webpush_fanout(notification)
            sent += 1
        else:
            notification.mark_failed(detail)
            failed += 1

    remaining = Notification.objects.filter(status=Notification.Status.PENDING).count()
    summary = {
        "sent": sent,
        "failed": failed,
        "webpush": pushed,
        "remaining": remaining,
    }
    if sent or failed or pushed:
        logger.info("Notifications traitées : %s", summary)
    return summary


def send_notification(notification) -> bool:
    """Envoie une notification précise, sans attendre la file (débogage)."""
    ok, detail = _deliver(notification)
    if ok:
        notification.mark_sent()
        if notification.channel == Notification.Channel.IN_APP:
            _webpush_fanout(notification)
    else:
        notification.mark_failed(detail)
    return ok


def _webpush_fanout(notification) -> int:
    """Pousse le message vers les navigateurs du destinataire (meilleur effort).

    Les souscriptions mortes (endpoint 404/410) sont désactivées, jamais
    supprimées, afin de garder la trace de l'appareil.
    """
    from django.utils import timezone

    from . import providers
    from .models import PushSubscription

    if notification.recipient_id is None:
        return 0
    dead_endpoints = []
    delivered = 0
    for subscription in PushSubscription.objects.filter(
        user=notification.recipient, is_active=True
    ):
        ok, detail = providers.send_webpush(
            endpoint=subscription.endpoint,
            p256dh=subscription.p256dh,
            auth=subscription.auth,
            title=notification.title,
            body=notification.body,
            url="/notifications/",
        )
        if ok:
            delivered += 1
            subscription.last_seen = timezone.now()
            subscription.save(update_fields=["last_seen"])
        elif detail in {"push-http:404", "push-http:410"}:
            dead_endpoints.append(subscription.pk)
    if dead_endpoints:
        PushSubscription.objects.filter(pk__in=dead_endpoints, is_active=True).update(
            is_active=False
        )
    if delivered and notification.pushed_at is None:
        notification.pushed_at = timezone.now()
        notification.save(update_fields=["pushed_at", "updated_at"])
    return delivered


def _deliver(notification) -> tuple[bool, str]:
    """Pousse un message sur son canal ; ``mark_sent``/``mark_failed`` suivent."""
    # Mise à jour optimiste : deux workers ne partent pas en même temps.
    Notification.all_objects.filter(pk=notification.pk).update(
        attempts=notification.attempts + 1
    )
    notification.attempts += 1
    from . import providers

    if notification.channel == Notification.Channel.EMAIL:
        return providers.send_email(
            subject=notification.title,
            body=notification.body,
            to_email=notification.email,
        )
    if notification.channel == Notification.Channel.WHATSAPP:
        return providers.send_whatsapp(body=notification.body, phone=notification.phone)
    # Canal back-office : la ligne elle-même est la livraison.
    if notification.recipient_id is None:
        return False, _("Notification in-app sans destinataire.")
    return True, f"in-app:{notification.recipient_id}"


# ---------------------------------------------------------------------------
# Rappels de rendez-vous (jalon 5)
# ---------------------------------------------------------------------------


def schedule_appointment_reminders(appointment) -> list:
    """Planifie les rappels d'un rendez-vous (réglage ``APPOINTMENT_REMINDER_SCHEDULE``).

    Chaque réglage ``(code, heures avant, canaux)`` devient une notification
    en attente : WhatsApp + e-mail pour le patient, message in-app pour le
    praticien. Les moments déjà passés sont ignorés ; l'annulation du
    rendez-vous retire la file correspondante (voir :func:`cancel_appointment_reminders`).
    """
    created = []
    for code, hours_before, channels in settings.APPOINTMENT_REMINDER_SCHEDULE:
        scheduled_for = appointment.start_at - timezone.timedelta(hours=hours_before)
        if scheduled_for <= timezone.now():
            continue
        title, body = appointment_reminder_text(appointment, code=code)
        for channel in channels:
            channel = _normalized_channel(channel)
            if channel == Notification.Channel.IN_APP:
                notice = queue_notification(
                    clinic=appointment.clinic,
                    recipient=appointment.practitioner.user,
                    appointment=appointment,
                    channel=channel,
                    category=Notification.Category.APPOINTMENT_REMINDER,
                    title=_("Patient à venir : %(patient)s.")
                    % {
                        "patient": appointment.patient.display_name,
                    },
                    body=_("%(patient)s attendu le %(heure)s — motif : %(motif)s.")
                    % {
                        "patient": appointment.patient.display_name,
                        "heure": body.splitlines()[0] if body else "",
                        "motif": appointment.reason or _("consultation"),
                    },
                    scheduled_for=scheduled_for,
                )
            else:
                notice = queue_notification(
                    clinic=appointment.clinic,
                    patient=appointment.patient,
                    appointment=appointment,
                    channel=channel,
                    category=Notification.Category.APPOINTMENT_REMINDER,
                    title=title,
                    body=body,
                    scheduled_for=scheduled_for,
                )
            if notice is not None:
                created.append(notice)
    return created


def appointment_reminder_text(appointment, *, code: str = ""):
    """Titre et corps du rappel patient (« la veille » ou « bientôt »)."""
    label = (
        _("Rappel : rendez-vous la veille")
        if code.upper().startswith("J1")
        else (_("Rappel : rendez-vous bientôt"))
    )
    heure = timezone.localtime(appointment.start_at).strftime("%d/%m/%Y à %H:%M")
    title = _("[%(clinique)s] %(label)s") % {
        "clinique": appointment.clinic.name,
        "label": label,
    }
    body = (
        _("Bonjour %(patient)s,")
        + "\n\n"
        + _(
            "Vous avez rendez-vous à la clinique %(clinique)s "
            "le %(heure)s avec %(praticien)s."
        )
        + "\n"
        + _("Motif : %(motif)s.")
        + "\n\n"
        + _("À bientôt,")
        + "\n"
        + appointment.clinic.name
    ) % {
        "patient": appointment.patient.display_name,
        "clinique": appointment.clinic.name,
        "heure": heure,
        "praticien": appointment.practitioner_name,
        "motif": appointment.reason or _("consultation"),
    }
    return title, body


def cancel_appointment_reminders(appointment) -> int:
    """Retire de la file les rappels encore en attente (rendez-vous annulé)."""
    return Notification.objects.filter(
        appointment=appointment,
        status=Notification.Status.PENDING,
    ).update(status=Notification.Status.CANCELLED)


def send_imminent_reminders(*, window_hours: float = 1.0, only_clinics=None) -> dict:
    """Filet de sécurité : rappels des rendez-vous proches déjà partis de la file.

    Couvre les rendez-vous sans aucune notification de rappel en attente
    (créés avant le jalon 5, file annulée, échec de planification…) : un
    message in-app au praticien et, si possible, un e-mail au patient.
    """
    now = timezone.now()
    limit = now + timezone.timedelta(hours=window_hours)
    queued = 0
    for clinic in _clinics(only_clinics):
        for appointment in _pending_appointments(clinic, now=now, limit=limit):
            for notice in _queue_imminent(clinic, appointment):
                queued += 1 if notice is not None else 0
    summary = {"queued": queued}
    if queued:
        logger.info("Rappels imminents mis en file : %s", summary)
    return summary


def _queue_imminent(clinic, appointment) -> list:
    title_patient, body_patient = appointment_reminder_text(appointment, code="H1")
    notices = [
        queue_notification(
            clinic=clinic,
            recipient=appointment.practitioner.user,
            appointment=appointment,
            channel=Notification.Channel.IN_APP,
            category=Notification.Category.APPOINTMENT_REMINDER,
            title=_("Rendez-vous imminent : %(patient)s.")
            % {
                "patient": appointment.patient.display_name,
            },
            body=_("%(patient)s attendu à %(heure)s — motif : %(motif)s.")
            % {
                "patient": appointment.patient.display_name,
                "heure": timezone.localtime(appointment.start_at).strftime("%H:%M"),
                "motif": appointment.reason or _("consultation"),
            },
        ),
        queue_notification(
            clinic=clinic,
            patient=appointment.patient,
            appointment=appointment,
            channel=Notification.Channel.EMAIL,
            category=Notification.Category.APPOINTMENT_REMINDER,
            title=title_patient,
            body=body_patient,
        ),
    ]
    return [notice for notice in notices if notice is not None]


def _clinics(only_clinics=None):
    from apps.accounts.models import Clinic

    clinics = Clinic.objects.filter(is_active=True)
    if only_clinics is not None:
        clinics = clinics.filter(pk__in=[clinic.pk for clinic in only_clinics])
    return list(clinics)


def _pending_appointments(clinic, *, now, limit):
    """Rendez-vous à venir, jamais rappelés, sans rappel planifié (une clinique)."""
    from apps.appointments.models import Appointment

    return list(
        Appointment.objects.filter(clinic=clinic)
        .filter(status__in=(Appointment.Status.PENDING, Appointment.Status.CONFIRMED))
        .filter(reminder_sent_at__isnull=True)
        .filter(start_at__gte=now, start_at__lte=limit)
        .exclude(notifications__status=Notification.Status.PENDING)
        .select_related("patient", "practitioner", "clinic")
        .distinct()
    )
