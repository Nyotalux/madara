"""Vues web des consultations et ordonnances (jalon 4).

La consultation est l'acte médical : elle peut prolonger un rendez-vous de la
salle d'attente (``consultation-start``) ou se créer librement pour un patient.
Les règles de gestion vivent dans ``apps.consultations.services``.
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.appointments.models import Appointment
from apps.appointments.services import clinic_timezone, day_bounds
from apps.common.decorators import require_clinic, require_roles
from apps.common.pagination import paginate
from apps.patients.models import Patient

from . import services
from .forms import (
    ConsultationCancelForm,
    ConsultationFilterForm,
    ConsultationForm,
    ConsultationStartForm,
    PrescriptionForm,
    PrescriptionItemFormSet,
)
from .models import Consultation, Prescription

#: Lecture des consultations : tout le personnel soignant et l'accueil.
READ_ROLES = ("RECEPTION", "DOCTOR", "NURSE", "ADMIN")
#: Écriture des actes médicaux : médecin, infirmier, administrateur.
WRITE_ROLES = services.CONSULTATION_ROLES
#: Rôles dont la vue est restreinte à leurs propres consultations.
PRACTITIONER_SCOPED_ROLES = ("DOCTOR", "NURSE")


# ---------------------------------------------------------------------------
# Liste
# ---------------------------------------------------------------------------


@login_required
@require_clinic
@require_roles(*READ_ROLES)
def consultation_list(request):
    """Historique filtrable des consultations de la clinique."""
    clinic = request.clinic
    tz = clinic_timezone(clinic)
    form = ConsultationFilterForm(
        request.GET or None, clinic=clinic, practitioners=_practitioners(clinic)
    )
    queryset = _visible_consultations(request).select_related(
        "patient", "practitioner", "practitioner__user"
    )

    if form.is_valid():
        data = form.cleaned_data
        if data.get("status"):
            queryset = queryset.filter(status=data["status"])
        if data.get("kind"):
            queryset = queryset.filter(kind=data["kind"])
        if data.get("practitioner"):
            queryset = queryset.filter(practitioner=data["practitioner"])
        if data.get("du"):
            start, _ = day_bounds(data["du"], tz)
            queryset = queryset.filter(started_at__gte=start)
        if data.get("au"):
            _, end = day_bounds(data["au"], tz)
            queryset = queryset.filter(started_at__lt=end)
        needle = (data.get("q") or "").strip()
        if needle:
            queryset = queryset.filter(
                Q(patient__first_name__icontains=needle)
                | Q(patient__last_name__icontains=needle)
                | Q(patient__reference__icontains=needle)
                | Q(reference__icontains=needle)
                | Q(chief_complaint__icontains=needle)
                | Q(diagnosis__icontains=needle)
            )

    queryset = queryset.order_by("-started_at")
    today = timezone.localdate()
    base = _visible_consultations(request)
    stats = {
        "total": base.count(),
        "today": base.filter(started_at__date=today).count(),
        "in_progress": base.filter(status=Consultation.Status.IN_PROGRESS).count(),
        "completed": base.filter(status=Consultation.Status.COMPLETED).count(),
    }
    page = paginate(request, queryset)
    return render(
        request,
        "consultations/consultation_list.html",
        {
            "form": form,
            "consultations": page["page_obj"].object_list,
            "stats": stats,
            "result_count": queryset.count(),
            "can_write": _can_write(request),
            **page,
        },
    )


# ---------------------------------------------------------------------------
# Fiche
# ---------------------------------------------------------------------------


@login_required
@require_clinic
@require_roles(*READ_ROLES)
def consultation_detail(request, pk):
    consultation = get_object_or_404(
        _visible_consultations(request).select_related(
            "patient",
            "practitioner",
            "practitioner__user",
            "appointment",
            "created_by",
        ),
        pk=pk,
    )
    return render(
        request,
        "consultations/consultation_detail.html",
        {
            "consultation": consultation,
            "prescriptions": consultation.prescriptions.select_related(
                "practitioner__user"
            ).prefetch_related("items"),
            "vitals": _vitals_breakdown(consultation),
            "can_write": _can_write(request) and _is_owner(request, consultation),
            "can_complete": (
                consultation.can_be_completed
                and _can_write(request)
                and _is_owner(request, consultation)
            ),
            "can_cancel": (
                consultation.status == Consultation.Status.IN_PROGRESS
                and _can_write(request)
                and _is_owner(request, consultation)
            ),
        },
    )


# ---------------------------------------------------------------------------
# Démarrage depuis la salle d'attente ou en consultation libre
# ---------------------------------------------------------------------------


@login_required
@require_clinic
@require_roles(*WRITE_ROLES)
def consultation_start(request, pk=None):
    """Ouvre une consultation, éventuellement à partir d'un rendez-vous."""
    clinic = request.clinic
    appointment = None
    if pk is not None:
        appointment = get_object_or_404(
            Appointment.objects.filter(clinic=clinic).select_related(
                "patient", "practitioner__user"
            ),
            pk=pk,
        )
        if not _can_start(request, appointment):
            raise PermissionDenied(_("Vous ne pouvez pas démarrer cette consultation."))
        existing = Consultation.all_objects.filter(
            appointment=appointment, is_active=True
        ).first()
        if existing is not None:
            messages.info(
                request, _("Une consultation est déjà ouverte pour ce rendez-vous.")
            )
            return redirect(reverse("web:consultation-detail", args=[existing.pk]))

    initial = {}
    lock_practitioner = None
    if appointment is None:
        patient_id = request.GET.get("patient")
        if patient_id:
            patient = Patient.objects.filter(clinic=clinic, pk=patient_id).first()
            if patient is not None:
                initial["patient"] = patient.pk
        if _is_practitioner_scoped(request):
            lock_practitioner = request.clinic_membership
        elif request.GET.get("practitioner"):
            initial["practitioner"] = request.GET["practitioner"]
    elif _is_practitioner_scoped(request) and appointment.practitioner_id != (
        request.clinic_membership.pk
    ):
        raise PermissionDenied(_("Ce rendez-vous concerne un autre praticien."))

    form = ConsultationStartForm(
        request.POST or None,
        clinic=clinic,
        appointment=appointment,
        lock_practitioner=lock_practitioner,
        initial=initial,
    )
    if request.method == "POST" and form.is_valid():
        practitioner = form.cleaned_data["practitioner"]
        try:
            consultation = services.start_consultation(
                clinic=clinic,
                patient=form.cleaned_data["patient"],
                practitioner=practitioner,
                appointment=appointment,
                kind=form.cleaned_data["kind"],
                chief_complaint=form.cleaned_data.get("chief_complaint", ""),
                user=request.user,
            )
        except ValidationError as exc:
            form.add_error(None, exc.messages[0] if exc.messages else str(exc))
        else:
            messages.success(
                request,
                _("Consultation %(reference)s démarrée.")
                % {"reference": consultation.reference},
            )
            return redirect(reverse("web:consultation-detail", args=[consultation.pk]))

    return render(
        request,
        "consultations/consultation_start.html",
        {
            "form": form,
            "appointment": appointment,
            "cancel_url": _start_cancel_url(appointment),
        },
    )


# ---------------------------------------------------------------------------
# Compte rendu
# ---------------------------------------------------------------------------


@login_required
@require_clinic
@require_roles(*WRITE_ROLES)
def consultation_edit(request, pk):
    consultation = get_object_or_404(
        _visible_consultations(request).select_related("patient", "practitioner__user"),
        pk=pk,
    )
    if not _is_owner(request, consultation):
        raise PermissionDenied(_("Vous ne pouvez modifier que vos consultations."))
    if consultation.status == Consultation.Status.CANCELLED:
        messages.warning(
            request, _("Une consultation annulée ne peut pas être modifiée.")
        )
        return redirect(reverse("web:consultation-detail", args=[consultation.pk]))

    form = ConsultationForm(request.POST or None, instance=consultation)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(
            request,
            _("Consultation %(reference)s mise à jour.")
            % {"reference": consultation.reference},
        )
        return redirect(reverse("web:consultation-detail", args=[consultation.pk]))

    return render(
        request,
        "consultations/consultation_form.html",
        {
            "form": form,
            "consultation": consultation,
            "cancel_url": reverse("web:consultation-detail", args=[consultation.pk]),
        },
    )


@login_required
@require_clinic
@require_roles(*WRITE_ROLES)
def consultation_complete(request, pk):
    """Clôt la consultation et termine le rendez-vous associé."""
    consultation = get_object_or_404(_visible_consultations(request), pk=pk)
    if not _is_owner(request, consultation):
        raise PermissionDenied(_("Vous ne pouvez clôturer que vos consultations."))
    if request.method != "POST":
        return redirect(reverse("web:consultation-detail", args=[consultation.pk]))

    services.complete_consultation(consultation, user=request.user)
    messages.success(
        request,
        _("Consultation %(reference)s terminée.") % {"reference": consultation.reference},
    )
    return redirect(reverse("web:consultation-detail", args=[consultation.pk]))


@login_required
@require_clinic
@require_roles(*WRITE_ROLES)
def consultation_cancel(request, pk):
    consultation = get_object_or_404(_visible_consultations(request), pk=pk)
    if not _is_owner(request, consultation):
        raise PermissionDenied(_("Vous ne pouvez annuler que vos consultations."))
    form = ConsultationCancelForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            services.cancel_consultation(
                consultation,
                reason=form.cleaned_data.get("reason", ""),
                user=request.user,
            )
        except ValidationError as exc:
            form.add_error(None, exc.messages[0])
        else:
            messages.warning(
                request,
                _("Consultation %(reference)s annulée.")
                % {"reference": consultation.reference},
            )
            return redirect(reverse("web:consultation-detail", args=[consultation.pk]))

    return render(
        request,
        "consultations/consultation_cancel.html",
        {"consultation": consultation, "form": form},
    )


# ---------------------------------------------------------------------------
# Ordonnances
# ---------------------------------------------------------------------------


@login_required
@require_clinic
@require_roles(*WRITE_ROLES)
def prescription_create(request, pk):
    """Rédige une ordonnance et ses lignes pour une consultation."""
    consultation = get_object_or_404(
        _visible_consultations(request).select_related("patient", "practitioner__user"),
        pk=pk,
    )
    if not _is_owner(request, consultation):
        raise PermissionDenied(_("Vous ne pouvez prescrire que sur vos consultations."))
    if consultation.status == Consultation.Status.CANCELLED:
        messages.warning(
            request, _("Impossible de prescrire sur une consultation annulée.")
        )
        return redirect(reverse("web:consultation-detail", args=[consultation.pk]))

    form = PrescriptionForm(request.POST or None)
    formset = PrescriptionItemFormSet(
        request.POST or None, instance=Prescription(), prefix="items"
    )
    if request.method == "POST" and form.is_valid() and formset.is_valid():
        items = [
            row
            for row in formset.cleaned_data
            if row and not row.get("DELETE") and row.get("medication")
        ]
        if not items:
            formset._non_form_errors = formset.error_class(
                [_("Ajoutez au moins un médicament.")]
            )
        else:
            prescription = services.add_prescription(
                consultation,
                issued_at=form.cleaned_data.get("issued_at"),
                notes=form.cleaned_data.get("notes", ""),
                items=items,
                user=request.user,
            )
            messages.success(
                request,
                _("Ordonnance %(reference)s créée.")
                % {"reference": prescription.reference},
            )
            return redirect(reverse("web:consultation-detail", args=[consultation.pk]))

    return render(
        request,
        "consultations/prescription_form.html",
        {
            "consultation": consultation,
            "form": form,
            "formset": formset,
            "cancel_url": reverse("web:consultation-detail", args=[consultation.pk]),
        },
    )


@login_required
@require_clinic
@require_roles(*READ_ROLES)
def prescription_print(request, pk):
    """Ordonnance imprimable à remettre au patient."""
    prescription = get_object_or_404(
        Prescription.objects.filter(
            clinic=request.clinic, pk=pk, is_active=True
        ).select_related("patient", "practitioner__user", "consultation"),
        pk=pk,
    )
    if not _can_read_prescription(request, prescription):
        raise PermissionDenied(_("Cette ordonnance ne vous est pas accessible."))
    return render(
        request,
        "consultations/prescription_print.html",
        {
            "prescription": prescription,
            "items": prescription.items.all(),
            "clinic": request.clinic,
        },
    )


# ---------------------------------------------------------------------------
# Aides
# ---------------------------------------------------------------------------


def _practitioners(clinic):
    from apps.appointments.forms import practitioners_of

    return practitioners_of(clinic)


def _visible_consultations(request):
    """Consultations visibles : les praticiens ne voient que les leurs."""
    queryset = Consultation.objects.filter(clinic=request.clinic)
    if _is_practitioner_scoped(request):
        queryset = queryset.filter(practitioner=request.clinic_membership)
    return queryset


def _can_read_prescription(request, prescription) -> bool:
    if not _is_practitioner_scoped(request):
        return True
    return prescription.practitioner_id == request.clinic_membership.pk


def _is_practitioner_scoped(request) -> bool:
    membership = getattr(request, "clinic_membership", None)
    if membership is None:
        return False
    if request.user.is_platform_staff:
        return False
    return membership.role in PRACTITIONER_SCOPED_ROLES


def _is_owner(request, consultation) -> bool:
    """Le praticien n'agit que sur ses propres consultations ; l'admin sur tout."""
    membership = getattr(request, "clinic_membership", None)
    if membership is None:
        return False
    if membership.role == "ADMIN":
        return True
    return membership.pk == consultation.practitioner_id


def _can_write(request) -> bool:
    membership = getattr(request, "clinic_membership", None)
    if membership is None:
        return False
    if request.user.is_platform_staff:
        return True
    return membership.role in WRITE_ROLES


def _can_start(request, appointment) -> bool:
    if not _can_write(request):
        return False
    if _is_practitioner_scoped(request):
        return appointment.practitioner_id == request.clinic_membership.pk
    return True


def _start_cancel_url(appointment):
    if appointment is not None:
        return reverse("web:appointment-detail", args=[appointment.pk])
    return reverse("web:consultation-list")


def _vitals_breakdown(consultation):
    """Constantes présentes, prêtes pour l'affichage (libellé, valeur)."""
    rows = []
    if consultation.blood_pressure_display:
        rows.append(
            {
                "label": _("Tension artérielle"),
                "value": consultation.blood_pressure_display,
            }
        )
    field_labels = {
        "temperature_c": (_("Température"), "°C"),
        "heart_rate": (_("Fréquence cardiaque"), "bpm"),
        "respiratory_rate": (_("Fréquence respiratoire"), "/min"),
        "oxygen_saturation": (_("Saturation en O₂"), "%"),
        "blood_glucose": (_("Glycémie"), "g/L"),
        "weight_kg": (_("Poids"), "kg"),
        "height_cm": (_("Taille"), "cm"),
    }
    for field, (label, unit) in field_labels.items():
        value = getattr(consultation, field)
        if value is not None:
            rows.append({"label": label, "value": f"{value} {unit}".strip()})
    bmi = consultation.bmi
    if bmi is not None:
        rows.append({"label": _("IMC"), "value": bmi})
    return rows
