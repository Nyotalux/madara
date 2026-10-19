"""Règles de gestion de l'agenda : conflits, créneaux libres, réservation.

Ces fonctions encapsulent la logique réutilisée par les vues web, l'API et les
tâches de fond. Elles acceptent toujours une clinique explicite et ne s'appuient
jamais sur le contexte ambiant, sauf pour le compteur de séquences.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.accounts.models import BlockedSlot, Membership, OpeningHour

from .models import Appointment

BOOKING_ROLES = ("RECEPTION", "DOCTOR", "NURSE", "ADMIN")


# ---------------------------------------------------------------------------
# Bornes temporelles
# ---------------------------------------------------------------------------


def day_bounds(day: date, tz=None):
    """Début et fin d'une journée de clinic (00:00 → 00:00 le lendemain)."""
    zone = tz or timezone.get_current_timezone()
    start = timezone.make_aware(datetime.combine(day, time.min), zone)
    return start, start + timedelta(days=1)


def week_bounds(day: date, first_weekday: int = 0, tz=None):
    """Semaine contenant ``day`` : lundi → dimanche."""
    start_date = day - timedelta(days=day.weekday() - first_weekday)
    start, _ = day_bounds(start_date, tz)
    return start, start + timedelta(days=7)


def clinic_timezone(clinic):
    """Fuseau horaire de la clinique : l'agenda suit l'heure du cabinet."""
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    name = getattr(clinic, "timezone", None)
    if not name:
        return timezone.get_current_timezone()
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return timezone.get_current_timezone()


def local_now(tz=None):
    zone = tz or timezone.get_current_timezone()
    return timezone.localtime(timezone.now(), zone)


# ---------------------------------------------------------------------------
# Conflits
# ---------------------------------------------------------------------------


def conflicts(
    *,
    practitioner: Membership,
    start_at,
    end_at,
    clinic=None,
    patient=None,
    exclude_pk=None,
) -> dict:
    """Chevauchements sur un créneau.

    Renvoie trois listes : rendez-vous du praticien, indisponibilités déclarées
    et rendez-vous du patient chez un autre praticien.
    """
    clinic = clinic or practitioner.clinic
    appointments = Appointment.all_objects.filter(
        clinic=clinic,
        practitioner=practitioner,
        status__in=Appointment.BLOCKING_STATUSES,
        start_at__lt=end_at,
        end_at__gt=start_at,
    ).exclude(pk=exclude_pk or 0)

    blocked = BlockedSlot.objects.filter(
        membership=practitioner, start_at__lt=end_at, end_at__gt=start_at
    )

    patient_bookings = Appointment.all_objects.none()
    if patient is not None and patient.pk:
        patient_bookings = (
            Appointment.all_objects.filter(
                clinic=clinic,
                patient=patient,
                status__in=Appointment.BLOCKING_STATUSES,
                start_at__lt=end_at,
                end_at__gt=start_at,
            )
            .exclude(pk=exclude_pk or 0)
            .exclude(practitioner=practitioner)
        )

    return {
        "appointments": list(appointments.order_by("start_at")),
        "blocked_slots": list(blocked.order_by("start_at")),
        "patient": patient_bookings.order_by("start_at").first(),
    }


# ---------------------------------------------------------------------------
# Créneaux disponibles
# ---------------------------------------------------------------------------


def available_slots(
    practitioner: Membership,
    day: date,
    *,
    clinic=None,
    slot_minutes: int | None = None,
    ignore_appointment=None,
    tz=None,
    include_past: bool = False,
) -> list[tuple[datetime, datetime]]:
    """Créneaux libres d'un praticien pour une journée.

    Déduit des horaires d'ouverture (jalon 1) moins les rendez-vous bloquants et
    les indisponibilités déclarées. Le pas de découpage est la durée de
    consultation du praticien, ou le créneau de la clinique.
    ``include_past`` conserve aussi les créneaux déjà écoulés : utile pour
    l'historique et les statistiques, jamais pour une nouvelle réservation.
    """
    zone = tz or timezone.get_current_timezone()
    clinic = clinic or practitioner.clinic
    day = _as_local_date(day, zone)

    step = slot_minutes or _slot_minutes(practitioner, clinic)
    if step < 5:
        step = 5

    windows = []
    for opening in OpeningHour.objects.filter(
        membership=practitioner, weekday=day.weekday(), is_closed=False
    ).order_by("start_time"):
        window_start = timezone.make_aware(
            datetime.combine(day, opening.start_time), zone
        )
        window_end = timezone.make_aware(datetime.combine(day, opening.end_time), zone)
        windows.append((window_start, window_end))

    if not windows:
        return []

    day_start, day_end = day_bounds(day, zone)
    busy = _busy_intervals(practitioner, day_start, day_end, ignore_appointment)
    now = local_now(zone)

    current_slot = ignore_appointment.start_at if ignore_appointment is not None else None

    slots = []
    for window_start, window_end in windows:
        cursor = window_start
        while cursor + timedelta(minutes=step) <= window_end:
            slot_end = cursor + timedelta(minutes=step)
            # Le rendez-vous en cours d'édition reste sélectionnable même si
            # son créneau est dans le passé ou occupé par lui-même.
            if cursor == current_slot:
                slots.append((cursor, slot_end))
            elif (include_past or cursor > now) and not _overlaps_any(
                cursor, slot_end, busy
            ):
                slots.append((cursor, slot_end))
            cursor += timedelta(minutes=step)
    return slots


def within_opening_hours(
    practitioner: Membership,
    start_at,
    end_at,
    *,
    clinic=None,
    tz=None,
) -> bool:
    """Le créneau est-il couvert par les horaires d'ouverture du praticien ?

    Les créneaux proposés par l'API et le formulaire en tiennent déjà compte ;
    ce contrôle protège les écritures directes (API, imports, console).
    """
    clinic = clinic or practitioner.clinic
    zone = tz or clinic_timezone(clinic)
    day = _as_local_date(start_at, zone)
    windows = []
    for opening in OpeningHour.objects.filter(
        membership=practitioner, weekday=day.weekday(), is_closed=False
    ):
        window_start = timezone.make_aware(
            datetime.combine(day, opening.start_time), zone
        )
        window_end = timezone.make_aware(datetime.combine(day, opening.end_time), zone)
        windows.append((window_start, window_end))
    if not windows:
        return False
    return any(
        window_start <= start_at and end_at <= window_end
        for window_start, window_end in windows
    )


def _slot_minutes(practitioner: Membership, clinic) -> int:
    profile = getattr(practitioner, "practitioner_profile", None)
    if profile is not None and profile.consultation_duration_minutes:
        return profile.consultation_duration_minutes
    return clinic.appointment_slot_minutes or 20


def _busy_intervals(practitioner, day_start, day_end, ignore_appointment=None) -> list:
    intervals = [
        (a.start_at, a.end_at)
        for a in Appointment.all_objects.filter(
            clinic=practitioner.clinic,
            practitioner=practitioner,
            status__in=Appointment.BLOCKING_STATUSES,
            start_at__lt=day_end,
            end_at__gt=day_start,
        )
        if ignore_appointment is None or a.pk != ignore_appointment
    ]
    intervals += [
        (b.start_at, b.end_at)
        for b in BlockedSlot.objects.filter(
            membership=practitioner, start_at__lt=day_end, end_at__gt=day_start
        )
    ]
    return sorted(intervals)


def _overlaps_any(start_at, end_at, intervals) -> bool:
    return any(
        start_at < busy_end and busy_start < end_at for busy_start, busy_end in intervals
    )


def _as_local_date(value, zone) -> date:
    if isinstance(value, datetime):
        return (
            timezone.localtime(value, zone).date()
            if timezone.is_aware(value)
            else value.date()
        )
    return value


# ---------------------------------------------------------------------------
# Agenda
# ---------------------------------------------------------------------------


def agenda_queryset(
    clinic, *, start, end, practitioner=None, statuses=None, with_cancelled=False
):
    """Rendez-vous d'une plage horaire, prêts pour l'affichage."""
    queryset = Appointment.objects.filter(
        clinic=clinic, start_at__lt=end, end_at__gt=start
    )
    if practitioner is not None:
        queryset = queryset.filter(practitioner=practitioner)
    if not with_cancelled:
        queryset = queryset.exclude(status=Appointment.Status.CANCELLED)
    elif statuses:
        queryset = queryset.filter(status__in=statuses)
    return queryset.select_related(
        "patient", "practitioner", "practitioner__user"
    ).order_by("start_at")


def appointments_by_day(queryset) -> dict:
    """Regroupe une queryset par date locale : ``{date: [rendez-vous]}``."""
    grouped: dict[date, list] = {}
    for appointment in queryset:
        grouped.setdefault(appointment.local_date, []).append(appointment)
    return grouped


def agenda_notice(clinic, membership, day=None) -> list:
    """Notifications de la synthèse du jour pour un praticien (jalon 5).

    Renvoie des notifications non enregistrées : message in-app (toujours)
    et e-mail (si un e-mail joignable existe). La tâche Celery les enregistre.
    """
    from apps.notifications.models import Notification

    if day is None:
        day = timezone.localdate(timezone.now(), clinic_timezone(clinic))
    start, end = day_bounds(day, clinic_timezone(clinic))
    if membership.role in {Membership.Role.DOCTOR, Membership.Role.NURSE}:
        appointments = agenda_queryset(
            clinic, start=start, end=end, practitioner=membership
        )
    else:
        # Administrateur : vue d'ensemble de la clinique.
        appointments = agenda_queryset(clinic, start=start, end=end)
    count = appointments.count()
    lines = [
        _("%(heure)s — %(patient)s (%(statut)s)")
        % {
            "heure": appointment.display_time,
            "patient": appointment.patient.display_name,
            "statut": appointment.get_status_display(),
        }
        for appointment in appointments[:20]
    ]
    body = (
        _("Bonjour %(nom)s,")
        + "\n\n"
        + _(
            "Votre agenda du %(jour)s à la clinique %(clinique)s "
            "compte %(count)s rendez-vous."
        )
        % {
            "nom": membership.user.full_name or "",
            "jour": day.strftime("%d/%m/%Y"),
            "clinique": clinic.name,
            "count": count,
        }
        + ("\n\n" + "\n".join(lines) if lines else "")
        + "\n\n"
        + _("— Madara")
    )
    title = _("Agenda du %(jour)s — %(clinique)s") % {
        "jour": day.strftime("%d/%m/%Y"),
        "clinique": clinic.name,
    }
    notices = [
        Notification(
            clinic=clinic,
            recipient=membership.user,
            channel=Notification.Channel.IN_APP,
            category=Notification.Category.AGENDA_DIGEST,
            title=title,
            body=body,
        )
    ]
    if _has_reachable_email(membership.user):
        notices.append(
            Notification(
                clinic=clinic,
                recipient=membership.user,
                channel=Notification.Channel.EMAIL,
                category=Notification.Category.AGENDA_DIGEST,
                title=title,
                body=body,
            )
        )
    return notices


def _has_reachable_email(user) -> bool:
    return bool(
        (getattr(user, "notification_email", "") or getattr(user, "email", "")).strip()
    )


# ---------------------------------------------------------------------------
# Réservation
# ---------------------------------------------------------------------------


@transaction.atomic
def book_appointment(
    *,
    clinic,
    patient,
    practitioner: Membership,
    start_at,
    end_at=None,
    kind=Appointment.Kind.CONSULTATION,
    reason: str = "",
    status=Appointment.Status.PENDING,
    notes: str = "",
    user=None,
    ignore_appointment=None,
) -> Appointment:
    """Réserve un créneau après contrôle des conflits (verrou pessimiste)."""
    if practitioner.clinic_id != clinic.pk:
        raise ValidationError(_("Le praticien n'exerce pas dans cette clinique."))
    if patient.clinic_id != clinic.pk:
        raise ValidationError(_("Le patient n'appartient pas à cette clinique."))
    if not practitioner.is_active:
        raise ValidationError(_("Ce praticien n'est plus actif."))

    if end_at is None:
        profile = getattr(practitioner, "practitioner_profile", None)
        minutes = (
            profile.consultation_duration_minutes
            if profile is not None and profile.consultation_duration_minutes
            else clinic.appointment_slot_minutes or 20
        )
        end_at = start_at + timedelta(minutes=minutes)

    if end_at <= start_at:
        raise ValidationError({"end_at": _("La fin doit être postérieure au début.")})

    if not within_opening_hours(practitioner, start_at, end_at, clinic=clinic):
        day = _as_local_date(start_at, clinic_timezone(clinic))
        raise ValidationError(
            _("Le créneau demandé est hors des horaires du praticien (%s).")
            % day.strftime("%d/%m/%Y")
        )

    # Les lignes concurrentes sont verrouillées : deux réservations simultanées
    # sur le même créneau ne peuvent pas passer entre les deux contrôles.
    list(
        Appointment.all_objects.select_for_update()
        .filter(
            clinic=clinic,
            practitioner=practitioner,
            status__in=Appointment.BLOCKING_STATUSES,
            start_at__lt=end_at,
            end_at__gt=start_at,
        )
        .exclude(pk=ignore_appointment.pk if ignore_appointment else 0)
        .values_list("pk", flat=True)
    )

    found = conflicts(
        practitioner=practitioner,
        start_at=start_at,
        end_at=end_at,
        clinic=clinic,
        patient=patient,
        exclude_pk=ignore_appointment.pk if ignore_appointment else None,
    )
    if found["appointments"]:
        raise ValidationError(_("Ce créneau est déjà pris par un autre rendez-vous."))
    if found["blocked_slots"]:
        raise ValidationError(
            _("Le praticien est indisponible sur ce créneau : %(reason)s.")
            % {"reason": found["blocked_slots"][0].reason or _("indisponibilité")}
        )
    if found["patient"]:
        raise ValidationError(_("Le patient a déjà un rendez-vous à cette heure."))

    appointment = Appointment(
        clinic=clinic,
        patient=patient,
        practitioner=practitioner,
        start_at=start_at,
        end_at=end_at,
        kind=kind,
        reason=reason,
        status=status,
        notes=notes,
        created_by=user if getattr(user, "is_authenticated", False) else None,
    )
    appointment.full_clean()
    appointment.save()
    _schedule_reminders(appointment)
    return appointment


def _schedule_reminders(appointment) -> None:
    """Planifie les rappels du jalon 5 (jamais bloquant pour la réservation)."""
    try:
        from apps.notifications.services import schedule_appointment_reminders

        schedule_appointment_reminders(appointment)
    except Exception:  # pragma: no cover - la file peut être indisponible
        from logging import getLogger

        getLogger(__name__).exception(
            "Rappels non planifiés pour %s.", appointment.reference
        )


@transaction.atomic
def reschedule(
    appointment: Appointment, *, start_at, end_at=None, user=None
) -> Appointment:
    """Déplace un rendez-vous, en revalidant les conflits."""
    if appointment.is_cancelled:
        raise ValidationError(_("Un rendez-vous annulé ne peut pas être déplacé."))

    clinic = appointment.clinic
    if end_at is None:
        end_at = start_at + timedelta(minutes=appointment.duration_minutes or 20)

    list(
        Appointment.all_objects.select_for_update()
        .filter(
            clinic=clinic,
            practitioner=appointment.practitioner,
            status__in=Appointment.BLOCKING_STATUSES,
            start_at__lt=end_at,
            end_at__gt=start_at,
        )
        .exclude(pk=appointment.pk)
        .values_list("pk", flat=True)
    )

    if not within_opening_hours(
        appointment.practitioner, start_at, end_at, clinic=clinic
    ):
        raise ValidationError(_("Le nouveau créneau est hors des horaires du praticien."))

    appointment.start_at = start_at
    appointment.end_at = end_at
    appointment.full_clean()
    appointment.save(update_fields=["start_at", "end_at", "updated_at"])
    _reschedule_reminders(appointment)
    return appointment


def _reschedule_reminders(appointment) -> None:
    """Un rendez-vous déplacé abandonne ses anciens rappels et réapparaît dans la file."""
    try:
        from apps.notifications.services import (
            cancel_appointment_reminders,
            schedule_appointment_reminders,
        )

        cancel_appointment_reminders(appointment)
        if appointment.status in (
            Appointment.Status.PENDING,
            Appointment.Status.CONFIRMED,
        ):
            schedule_appointment_reminders(appointment)
    except Exception:  # pragma: no cover - la file peut être indisponible
        from logging import getLogger

        getLogger(__name__).exception(
            "Rappels non replanifiés pour %s.", appointment.reference
        )


@transaction.atomic
def set_status(
    appointment: Appointment, status: str, *, reason: str = "", user=None
) -> Appointment:
    """Applique une transition de statut en journalisant l'annulation."""
    from apps.notifications.services import cancel_appointment_reminders

    appointment.transition_to(status, reason=reason, user=user)
    if status == Appointment.Status.CANCELLED:
        # Les rappels planifiés (jalon 5) ne partent plus : le créneau est libre.
        cancel_appointment_reminders(appointment)
    return appointment
