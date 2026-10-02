"""Vues web des dossiers patients (jalon 2)."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.appointments.models import Appointment
from apps.common.decorators import require_clinic, require_roles
from apps.common.pagination import paginate

from .forms import PatientArchiveForm, PatientFilterForm, PatientForm
from .models import Patient

CLINICAL_ROLES = ("RECEPTION", "DOCTOR", "NURSE", "ADMIN")
ARCHIVE_ROLES = ("ADMIN",)


# ---------------------------------------------------------------------------
# Liste et recherche
# ---------------------------------------------------------------------------


@login_required
@require_clinic
@require_roles(*CLINICAL_ROLES)
def patient_list(request):
    """Dossiers de la clinique courante, avec recherche et filtres."""
    clinic = request.clinic
    form = PatientFilterForm(request.GET or None)

    # Le manager ``objects`` masque déjà les archives : le filtre « archivés »
    # doit donc passer par ``all_objects``, sinon la liste resterait vide.
    status = form["status"].value() if form.is_bound else None
    queryset = Patient.objects.filter(clinic=clinic)
    if status == "archived":
        queryset = Patient.all_objects.filter(clinic=clinic, is_active=False)

    order_by = "last_name"
    if form.is_valid():
        needle = form.cleaned_q()
        if needle:
            queryset = queryset.filter(
                Q(reference__icontains=needle)
                | Q(first_name__icontains=needle)
                | Q(last_name__icontains=needle)
                | Q(phone__icontains=needle)
                | Q(cin__icontains=needle)
                | Q(email__icontains=needle)
            )
        gender = form.cleaned_data.get("gender")
        if gender:
            queryset = queryset.filter(gender=gender)
        city = form.cleaned_data.get("city")
        if city:
            queryset = queryset.filter(city__icontains=city)
        insured = form.cleaned_data.get("insured")
        if insured == "yes":
            queryset = queryset.filter(is_insured=True)
        elif insured == "no":
            queryset = queryset.filter(is_insured=False)
        order_by = form.cleaned_data.get("order_by") or order_by

    patients = queryset.select_related("created_by").order_by(order_by, "first_name")

    active_patients = Patient.objects.filter(clinic=clinic)
    today = timezone.localdate()
    stats = {
        "total": Patient.all_objects.filter(clinic=clinic).count(),
        "active": active_patients.count(),
        "archived": Patient.all_objects.filter(clinic=clinic, is_active=False).count(),
        "insured": active_patients.filter(is_insured=True).count(),
        "new_this_month": active_patients.filter(
            created_at__month=today.month,
            created_at__year=today.year,
        ).count(),
    }

    page = paginate(request, patients)
    context = {
        "form": form,
        "stats": stats,
        "patients": page["page_obj"].object_list,
        "result_count": patients.count(),
        **page,
    }
    return render(request, "patients/patient_list.html", context)


# ---------------------------------------------------------------------------
# Fiche
# ---------------------------------------------------------------------------


@login_required
@require_clinic
@require_roles(*CLINICAL_ROLES)
def patient_detail(request, pk):
    clinic = request.clinic
    patient = get_object_or_404(
        Patient.all_objects.filter(clinic=clinic).select_related("created_by"),
        pk=pk,
    )

    upcoming = (
        Appointment.objects.filter(patient=patient, start_at__gte=timezone.now())
        .exclude(status=Appointment.Status.CANCELLED)
        .select_related("practitioner__user")
        .order_by("start_at")[:3]
    )
    past = (
        Appointment.objects.filter(patient=patient, start_at__lt=timezone.now())
        .select_related("practitioner__user")
        .order_by("-start_at")[:5]
    )

    return render(
        request,
        "patients/patient_detail.html",
        {
            "patient": patient,
            "upcoming_appointments": upcoming,
            "past_appointments": past,
            "can_edit": request.clinic_membership is not None
            and request.clinic_membership.role in CLINICAL_ROLES,
            "can_archive": request.clinic_membership is not None
            and request.clinic_membership.role == "ADMIN",
        },
    )


# ---------------------------------------------------------------------------
# Création et modification
# ---------------------------------------------------------------------------


@login_required
@require_clinic
@require_roles(*CLINICAL_ROLES)
def patient_create(request):
    clinic = request.clinic
    form = PatientForm(request.POST or None, clinic=clinic)
    if request.method == "POST" and form.is_valid():
        patient = form.save(commit=False)
        patient.clinic = clinic
        patient.save()
        messages.success(
            request,
            _("Dossier %(reference)s créé.") % {"reference": patient.reference},
        )
        return redirect("web:patient-detail", pk=patient.pk)

    return render(
        request,
        "patients/patient_form.html",
        {"form": form, "patient": None, "cancel_url": reverse("web:patient-list")},
    )


@login_required
@require_clinic
@require_roles(*CLINICAL_ROLES)
def patient_edit(request, pk):
    clinic = request.clinic
    patient = get_object_or_404(Patient, pk=pk, clinic=clinic)
    form = PatientForm(request.POST or None, instance=patient, clinic=clinic)

    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(
            request,
            _("Dossier %(reference)s mis à jour.") % {"reference": patient.reference},
        )
        return redirect("web:patient-detail", pk=patient.pk)

    return render(
        request,
        "patients/patient_form.html",
        {
            "form": form,
            "patient": patient,
            "cancel_url": reverse("web:patient-detail", args=[patient.pk]),
        },
    )


# ---------------------------------------------------------------------------
# Archivage (réservé à l'administrateur)
# ---------------------------------------------------------------------------


@login_required
@require_clinic
@require_roles(*ARCHIVE_ROLES)
def patient_archive(request, pk):
    clinic = request.clinic
    patient = get_object_or_404(Patient, pk=pk, clinic=clinic)
    form = PatientArchiveForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        patient.archive(form.cleaned_data["reason"])
        messages.warning(
            request,
            _("Dossier %(reference)s archivé.") % {"reference": patient.reference},
        )
        return redirect("web:patient-list")

    return render(
        request,
        "patients/patient_archive.html",
        {"patient": patient, "form": form},
    )


@login_required
@require_clinic
@require_roles(*ARCHIVE_ROLES)
def patient_restore(request, pk):
    clinic = request.clinic
    patient = get_object_or_404(Patient.all_objects, pk=pk, clinic=clinic)
    if request.method != "POST":
        return redirect("web:patient-detail", pk=patient.pk)
    patient.restore()
    messages.success(
        request, _("Dossier %(reference)s restauré.") % {"reference": patient.reference}
    )
    return redirect("web:patient-detail", pk=patient.pk)


# ---------------------------------------------------------------------------
# Divers
# ---------------------------------------------------------------------------


@login_required
@require_clinic
@require_roles(*CLINICAL_ROLES)
def patient_autocomplete(request):
    """Recherche rapide pour l'accueil et les formulaires (jalon 3+)."""
    clinic = request.clinic
    needle = (request.GET.get("q") or "").strip()
    if len(needle) < 2:
        return render(request, "patients/_autocomplete.html", {"patients": []})

    patients = (
        Patient.objects.filter(clinic=clinic)
        .filter(
            Q(first_name__icontains=needle)
            | Q(last_name__icontains=needle)
            | Q(phone__icontains=needle)
            | Q(reference__icontains=needle)
        )
        .order_by("last_name", "first_name")[:10]
    )
    return render(
        request,
        "patients/_autocomplete.html",
        {"patients": patients, "needle": needle},
    )


@login_required
@require_clinic
@require_roles(*CLINICAL_ROLES)
def patient_summary(request, pk):
    """Fragment HTMX : synthèse de la fiche pour les panneaux latéraux."""
    clinic = request.clinic
    patient = get_object_or_404(Patient, pk=pk, clinic=clinic)
    return render(request, "patients/_summary.html", {"patient": patient})


@login_required
@require_clinic
@require_roles(*CLINICAL_ROLES)
def patient_print(request, pk):
    """Fiche imprimable (ordonnance de sortie, dossier à remettre au patient)."""
    clinic = request.clinic
    patient = get_object_or_404(Patient, pk=pk, clinic=clinic)
    doctors = clinic.memberships.filter(role="DOCTOR", is_active=True).select_related(
        "user"
    )
    return render(
        request,
        "patients/patient_print.html",
        {"patient": patient, "clinic": clinic, "doctors": doctors},
    )


@login_required
@require_clinic
@require_roles(*CLINICAL_ROLES)
def patient_stats(request):
    """Répartition par ville et par tranche d'âge (indicateurs du jalon 7)."""
    clinic = request.clinic
    queryset = Patient.objects.filter(clinic=clinic)
    by_city = (
        queryset.exclude(city="")
        .values("city")
        .annotate(total=Count("id"))
        .order_by("-total")[:10]
    )
    return render(
        request,
        "patients/_stats.html",
        {"by_city": by_city, "total": queryset.count()},
    )
