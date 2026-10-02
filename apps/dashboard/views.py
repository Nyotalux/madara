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

    context = {
        "clinic": clinic,
        "role_counts": role_counts,
        "role_labels": dict(Membership.Role.choices),
        "total_staff": sum(role_counts.values()),
        "doctors": clinic.memberships.filter(
            role=Membership.Role.DOCTOR, is_active=True
        ).select_related("user"),
        "today": timezone.localdate(),
    }
    return render(request, "dashboard/dashboard.html", context)
