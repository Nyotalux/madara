"""Tableau de bord et rapports de direction.

Les indicateurs financiers, médicaux et logistiques seront ajoutés au jalon 7 ;
le jalon 1 se limite à l'état des lieux de la clinique courante.
"""

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.shortcuts import render
from django.utils import timezone

from apps.accounts.models import Membership
from apps.appointments.models import Appointment
from apps.appointments.services import clinic_timezone, day_bounds
from apps.common.decorators import require_clinic


@login_required
@require_clinic
def dashboard(request):
    """Page d'accueil du personnel : clinique, équipe, raccourcis."""
    clinic = request.clinic

    staff_stats = (
        Membership.objects.filter(clinic=clinic)
        .values("role")
        .annotate(total=Count("id"))
    )
    role_counts = {row["role"]: row["total"] for row in staff_stats}

    tz = clinic_timezone(clinic)
    today = timezone.localdate()
    day_start, day_end = day_bounds(today, tz)
    today_appointments = (
        Appointment.objects.filter(
            clinic=clinic, start_at__lt=day_end, end_at__gt=day_start
        )
        .select_related("patient", "practitioner__user")
        .order_by("start_at")
    )
    membership = getattr(request, "clinic_membership", None)
    if (
        membership is not None
        and not request.user.is_platform_staff
        and membership.role in ("DOCTOR", "NURSE")
    ):
        today_appointments = today_appointments.filter(practitioner=membership)

    active_today = today_appointments.exclude(
        status__in=(Appointment.Status.DONE, Appointment.Status.CANCELLED)
    )
    next_appointment = (
        active_today.filter(start_at__gte=timezone.now()).first() or active_today.first()
    )
    waiting_count = active_today.filter(status=Appointment.Status.ARRIVED).count()

    context = {
        "clinic": clinic,
        "today_appointments": today_appointments,
        "today_count": today_appointments.count(),
        "waiting_count": waiting_count,
        "next_appointment": next_appointment,
        "role_counts": role_counts,
        "role_labels": dict(Membership.Role.choices),
        "total_staff": sum(role_counts.values()),
        "doctors": clinic.memberships.filter(
            role=Membership.Role.DOCTOR, is_active=True
        ).select_related("user"),
        "today": today,
    }
    return render(request, "dashboard/dashboard.html", context)
