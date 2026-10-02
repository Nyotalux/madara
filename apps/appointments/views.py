"""Vues web de l'agenda (jalon 3).

Trois lectures : la journée, la semaine et la file d'attente. Les opérations
(prendre, déplacer, annuler, changer le statut) passent par des formulaires qui
délèguent le contrôle des conflits à ``apps.appointments.services``.
"""

from __future__ import annotations

from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.accounts.models import Membership
from apps.common.decorators import require_clinic, require_roles
from apps.common.pagination import paginate
from apps.patients.models import Patient

from . import services
from .forms import (
    AppointmentFilterForm,
    AppointmentForm,
    AppointmentStatusForm,
    SlotPickerForm,
    parse_day,
    practitioners_of,
)
from .models import Appointment

BOOKING_ROLES = ("RECEPTION", "DOCTOR", "NURSE", "ADMIN")

#: Un médecin voit son propre agenda ; l'accueil et l'administrateur voient
#: toute la clinique (et peuvent filtrer par praticien).
PRACTITIONER_SCOPED_ROLES = (Membership.Role.DOCTOR, Membership.Role.NURSE)


# ---------------------------------------------------------------------------
# Lecture de l'agenda
# ---------------------------------------------------------------------------


@login_required
@require_clinic
@require_roles(*BOOKING_ROLES)
def agenda(request, vue="jour"):
    """Journée en cours, avec le planning de chaque praticien.

    ``vue=semaine`` affiche les sept jours ; l'URL ``/agenda/semaine/`` fournit
    ce mode par défaut.
    """
    clinic = request.clinic
    tz = services.clinic_timezone(clinic)
    day = parse_day(request.GET.get("date"), timezone.localtime(tz).date())
    view = request.GET.get("vue") or vue
    if view not in {"jour", "semaine"}:
        view = "jour"

    practitioners = practitioners_of(clinic)
    form = AppointmentFilterForm(
        request.GET or None, clinic=clinic, practitioners=practitioners
    )
    selected = _selected_practitioner(request, form, practitioners)

    start, end = (
        services.week_bounds(day, tz=tz)
        if view == "semaine"
        else services.day_bounds(day, tz)
    )
    appointments = services.agenda_queryset(
        clinic, start=start, end=end, practitioner=selected
    )
    appointments = _apply_filters(appointments, form)

    grouped = services.appointments_by_day(appointments)
    context = {
        "clinic": clinic,
        "day": day,
        "view": view,
        "form": form,
        "practitioners": practitioners,
        "selected_practitioner": selected,
        "appointments": appointments,
        "grouped": grouped,
        "days": _week_days(day, grouped, tz),
        "stats": _agenda_stats(appointments),
        "prev_day": day - timedelta(days=7 if view == "semaine" else 1),
        "next_day": day + timedelta(days=7 if view == "semaine" else 1),
        "today": timezone.localdate(),
        "restricted": _is_restricted(request),
    }
    return render(request, "appointments/agenda.html", context)


@login_required
@require_clinic
@require_roles(*BOOKING_ROLES)
def waiting_room(request):
    """File d'attente : rendez-vous du jour arrivés ou confirmés."""
    clinic = request.clinic
    tz = services.clinic_timezone(clinic)
    day = parse_day(request.GET.get("date"), timezone.localtime(tz).date())
    start, end = services.day_bounds(day, tz)

    appointments = services.agenda_queryset(
        clinic,
        start=start,
        end=end,
        practitioner=request.clinic_membership if _is_restricted(request) else None,
    ).filter(status__in=[Appointment.Status.ARRIVED, Appointment.Status.CONFIRMED])
    context = {
        "clinic": clinic,
        "day": day,
        "waiting": appointments.filter(status=Appointment.Status.ARRIVED),
        "expected": appointments.filter(status=Appointment.Status.CONFIRMED),
        "in_progress": appointments.filter(status=Appointment.Status.IN_PROGRESS),
        "today": timezone.localdate(),
        "restricted": _is_restricted(request),
    }
    return render(request, "appointments/waiting_room.html", context)


@login_required
@require_clinic
@require_roles(*BOOKING_ROLES)
def appointment_list(request):
    """Historique filtrable (utilisé par les recherches et l'export jalon 7)."""
    clinic = request.clinic
    tz = services.clinic_timezone(clinic)
    practitioners = practitioners_of(clinic)
    form = AppointmentFilterForm(
        request.GET or None, clinic=clinic, practitioners=practitioners
    )
    selected = _selected_practitioner(request, form, practitioners)

    params = request.GET
    start, end = _range_from_params(params, tz)
    queryset = services.agenda_queryset(
        clinic,
        start=start,
        end=end,
        practitioner=selected,
        with_cancelled=params.get("annules") == "1",
    )
    queryset = _apply_filters(queryset, form)
    queryset = queryset.order_by("-start_at")

    page = paginate(request, queryset, per_page=50)
    context = {
        "clinic": clinic,
        "form": form,
        "appointments": page["page_obj"].object_list,
        "start": timezone.localtime(start, tz).date(),
        "end": timezone.localtime(end - timedelta(seconds=1), tz).date(),
        "selected_practitioner": selected,
        "restricted": _is_restricted(request),
        **page,
    }
    return render(request, "appointments/appointment_list.html", context)


# ---------------------------------------------------------------------------
# Fiche d'un rendez-vous
# ---------------------------------------------------------------------------


@login_required
@require_clinic
@require_roles(*BOOKING_ROLES)
def appointment_detail(request, pk):
    clinic = request.clinic
    appointment = get_object_or_404(
        _visible_appointments(request, clinic).select_related(
            "patient", "practitioner", "practitioner__user", "created_by"
        ),
        pk=pk,
    )
    status_form = AppointmentStatusForm(appointment=appointment)
    other_appointments = (
        Appointment.objects.filter(patient=appointment.patient)
        .exclude(pk=appointment.pk)
        .select_related("practitioner__user")
        .order_by("-start_at")[:5]
    )
    labels = dict(Appointment.Status.choices)
    timeline = [
        {
            "value": value,
            "label": labels[value],
            "is_current": value == appointment.status,
            "is_past": _status_order(value) <= _status_order(appointment.status),
        }
        for value in Appointment.Status.values
    ]
    return render(
        request,
        "appointments/appointment_detail.html",
        {
            "appointment": appointment,
            "status_form": status_form,
            "cancellation_log": appointment.cancellations.select_related("cancelled_by"),
            "can_edit": _can_edit(request, appointment),
            "can_manage": _can_manage(request, appointment),
            "other_appointments": other_appointments,
            "timeline": timeline,
        },
    )


#: Ordre logique du parcours d'un rendez-vous, pour la frise de la fiche.
STATUS_FLOW = (
    Appointment.Status.PENDING,
    Appointment.Status.CONFIRMED,
    Appointment.Status.ARRIVED,
    Appointment.Status.IN_PROGRESS,
    Appointment.Status.DONE,
)


def _status_order(status: str) -> int:
    try:
        return STATUS_FLOW.index(status)
    except ValueError:
        return -1


@login_required
@require_clinic
@require_roles(*BOOKING_ROLES)
def appointment_create(request):
    """Prise de rendez-vous, avec patient existant ou création au passage."""
    clinic = request.clinic
    initial = {}
    patient_id = request.GET.get("patient")
    if patient_id:
        patient = Patient.objects.filter(clinic=clinic, pk=patient_id).first()
        if patient is not None:
            initial["patient"] = patient.pk
    if request.GET.get("praticien"):
        initial["practitioner"] = request.GET["praticien"]
    if request.GET.get("date"):
        initial["day"] = parse_day(request.GET["date"])

    locked_practitioner = None
    if _is_restricted(request):
        # Un médecin ou un infirmier réserve pour lui-même, pas pour un collègue.
        locked_practitioner = request.clinic_membership
        initial["practitioner"] = locked_practitioner.pk

    form = AppointmentForm(
        request.POST or None,
        clinic=clinic,
        initial=initial,
        lock_practitioner=locked_practitioner,
    )
    if request.method == "POST" and form.is_valid():
        appointment = form.save()
        messages.success(
            request,
            _("Rendez-vous %(reference)s créé (%(slot)s).")
            % {
                "reference": appointment.reference,
                "slot": appointment.display_time,
            },
        )
        return redirect(reverse("web:appointment-detail", args=[appointment.pk]))

    return render(
        request,
        "appointments/appointment_form.html",
        {
            "form": form,
            "appointment": None,
            "locked_practitioner": locked_practitioner,
            "cancel_url": reverse("web:agenda"),
        },
    )


@login_required
@require_clinic
@require_roles(*BOOKING_ROLES)
def appointment_edit(request, pk):
    clinic = request.clinic
    appointment = get_object_or_404(_visible_appointments(request, clinic), pk=pk)
    if not _can_edit(request, appointment):
        messages.error(request, _("Vous ne pouvez modifier que vos propres rendez-vous."))
        return redirect(reverse("web:appointment-detail", args=[appointment.pk]))

    form = AppointmentForm(request.POST or None, clinic=clinic, instance=appointment)
    if request.method == "POST" and form.is_valid():
        appointment = form.save()
        messages.success(
            request,
            _("Rendez-vous %(reference)s mis à jour.")
            % {"reference": appointment.reference},
        )
        return redirect(reverse("web:appointment-detail", args=[appointment.pk]))

    return render(
        request,
        "appointments/appointment_form.html",
        {
            "form": form,
            "appointment": appointment,
            "cancel_url": reverse("web:appointment-detail", args=[appointment.pk]),
        },
    )


@login_required
@require_clinic
@require_roles(*BOOKING_ROLES)
def appointment_status(request, pk):
    """Change le statut (arrivé, en consultation, terminé, absent, annulé)."""
    clinic = request.clinic
    appointment = get_object_or_404(_visible_appointments(request, clinic), pk=pk)
    if not _can_manage(request, appointment):
        raise PermissionDenied(_("Vous ne pouvez pas modifier cet agenda."))

    form = AppointmentStatusForm(request.POST or None, appointment=appointment)
    if request.method == "POST" and form.is_valid():
        status = form.cleaned_data["status"]
        services.set_status(
            appointment,
            status,
            reason=form.cleaned_data.get("reason", ""),
            user=request.user,
        )
        if status == Appointment.Status.CANCELLED:
            messages.warning(request, _("Rendez-vous annulé."))
        else:
            messages.success(
                request,
                _("Statut mis à jour : %s.") % appointment.get_status_display(),
            )
        return redirect(reverse("web:appointment-detail", args=[appointment.pk]))

    return render(
        request,
        "appointments/appointment_status.html",
        {"appointment": appointment, "form": form},
    )


@login_required
@require_clinic
@require_roles(*BOOKING_ROLES)
def appointment_quick_status(request, pk, status):
    """Action rapide HTMX : arrivée immédiate, fin de consultation…"""
    appointment = get_object_or_404(
        Appointment.objects.filter(clinic=request.clinic), pk=pk
    )
    if not _can_manage(request, appointment):
        raise PermissionDenied(_("Vous ne pouvez pas modifier cet agenda."))
    try:
        services.set_status(appointment, status, user=request.user)
    except ValidationError as exc:
        messages.error(request, exc.messages[0])
    else:
        messages.success(
            request,
            _("%(patient)s : %s.")
            % {
                "patient": appointment.patient.display_name,
                "status": appointment.get_status_display(),
            },
        )
    return redirect(reverse("web:waiting-room"))


# ---------------------------------------------------------------------------
# Fragments HTMX
# ---------------------------------------------------------------------------


@login_required
@require_clinic
@require_roles(*BOOKING_ROLES)
def available_slots(request):
    """Créneaux libres d'un praticien pour une date (fragment de formulaire)."""
    clinic = request.clinic
    form = SlotPickerForm(request.GET or None, clinic=clinic)
    if not form.is_valid():
        return render(
            request,
            "appointments/_slots.html",
            {"slots": [], "practitioner": None, "day": None},
        )

    practitioner = form.cleaned_data["practitioner"]
    day = form.cleaned_data["day"]
    tz = services.clinic_timezone(clinic)
    slots = services.available_slots(
        practitioner,
        day,
        clinic=clinic,
        tz=tz,
        ignore_appointment=_appointment_from_request(request, clinic),
    )
    return render(
        request,
        "appointments/_slots.html",
        {
            "slots": slots,
            "practitioner": practitioner,
            "day": day,
            "closed": _is_closed(practitioner, day),
        },
    )


# ---------------------------------------------------------------------------
# Aides
# ---------------------------------------------------------------------------


def _visible_appointments(request, clinic):
    """Restreint aux rendez-vous que l'utilisateur a le droit de consulter."""
    queryset = Appointment.objects.filter(clinic=clinic)
    if _is_restricted(request):
        queryset = queryset.filter(practitioner=request.clinic_membership)
    return queryset


def _is_restricted(request) -> bool:
    """Un médecin ou infirmier ne voit que son propre agenda."""
    membership = getattr(request, "clinic_membership", None)
    if membership is None:
        return False
    if request.user.is_platform_staff:
        return False
    return membership.role in PRACTITIONER_SCOPED_ROLES


def _can_edit(request, appointment) -> bool:
    membership = getattr(request, "clinic_membership", None)
    if membership is None:
        return False
    if membership.role in {"RECEPTION", "ADMIN"}:
        return not appointment.is_past
    return membership.pk == appointment.practitioner_id and not appointment.is_past


def _can_manage(request, appointment) -> bool:
    """Accueil et administrateur agissent sur tout ; le praticien sur le sien."""
    membership = getattr(request, "clinic_membership", None)
    if membership is None:
        return False
    if membership.role in {"RECEPTION", "ADMIN"}:
        return True
    return membership.pk == appointment.practitioner_id


def _selected_practitioner(request, form, practitioners):
    """Praticien filtré, en tenant compte de la restriction par rôle."""
    if _is_restricted(request):
        return request.clinic_membership
    value = request.GET.get("practitioner")
    if not value:
        return None
    return practitioners.filter(pk=value).first()


def _apply_filters(queryset, form):
    if not form.is_valid():
        return queryset
    data = form.cleaned_data
    if data.get("status"):
        queryset = queryset.filter(status=data["status"])
    if data.get("kind"):
        queryset = queryset.filter(kind=data["kind"])
    needle = (data.get("q") or "").strip()
    if needle:
        queryset = queryset.filter(
            Q(patient__first_name__icontains=needle)
            | Q(patient__last_name__icontains=needle)
            | Q(patient__phone__icontains=needle)
            | Q(reference__icontains=needle)
        )
    return queryset


def _range_from_params(params, tz):
    start_day = parse_day(params.get("du"), None)
    end_day = parse_day(params.get("au"), None)
    if start_day and end_day:
        start, _ = services.day_bounds(start_day, tz)
        _, end = services.day_bounds(end_day, tz)
        return start, end
    if start_day:
        return services.day_bounds(start_day, tz)
    if end_day:
        start, _ = services.day_bounds(end_day, tz)
        _, end = services.day_bounds(end_day + timedelta(days=1), tz)
        return start, end
    today = timezone.localtime(tz).date()
    return services.day_bounds(today - timedelta(days=7), tz)


def _week_days(day, grouped, tz):
    """Les sept jours de la semaine avec leurs rendez-vous."""
    start, _ = services.week_bounds(day, tz=tz)
    days = []
    for offset in range(7):
        current = start + timedelta(days=offset)
        current_date = timezone.localtime(current, tz).date()
        days.append(
            {
                "date": current_date,
                "label": current.strftime("%a %d/%m"),
                "count": len(grouped.get(current_date, [])),
                "appointments": grouped.get(current_date, []),
            }
        )
    return days


def _agenda_stats(appointments):
    """Compteurs par statut, prêts pour l'affichage."""
    counters = dict.fromkeys(Appointment.Status.values, 0)
    for appointment in appointments:
        counters[appointment.status] += 1
    labels = dict(Appointment.Status.choices)
    return [
        {"status": status, "label": labels[status], "count": counters[status]}
        for status in Appointment.Status.values
    ]


def _is_closed(practitioner, day) -> bool:
    from apps.accounts.models import OpeningHour

    return not OpeningHour.objects.filter(
        membership=practitioner, weekday=day.weekday(), is_closed=False
    ).exists()


def _appointment_from_request(request, clinic):
    """Rendez-vous en cours d'édition, pour ne pas perdre son propre créneau."""
    appointment_id = request.GET.get("appointment") or request.POST.get("appointment")
    if not appointment_id:
        return None
    return Appointment.objects.filter(clinic=clinic, pk=appointment_id).first()
