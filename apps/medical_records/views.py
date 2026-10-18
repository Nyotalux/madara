"""Vues web du dossier médical : antécédents, documents et partages (jalon 4)."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from apps.common.decorators import require_clinic, require_roles
from apps.patients.models import Patient

from . import services
from .forms import (
    MedicalDocumentForm,
    MedicalRecordForm,
    MedicalRecordShareForm,
)
from .models import MedicalDocument, MedicalRecordShare

#: Lecture du dossier : tout le personnel soignant et l'accueil.
READ_ROLES = ("RECEPTION", "DOCTOR", "NURSE", "ADMIN")
#: Écriture des pièces : accueil (scans), médecin, infirmier, administrateur.
WRITE_ROLES = ("RECEPTION", "DOCTOR", "NURSE", "ADMIN")


# ---------------------------------------------------------------------------
# Dossier
# ---------------------------------------------------------------------------


@login_required
@require_clinic
@require_roles(*READ_ROLES)
def record_detail(request, pk):
    """Dossier médical d'un patient (créé à la volée s'il n'existe pas)."""
    patient = get_object_or_404(Patient.all_objects, clinic=request.clinic, pk=pk)
    record = services.ensure_record(patient)
    return render(
        request,
        "medical_records/record_detail.html",
        {
            "patient": patient,
            "record": record,
            "documents": record.documents.filter(is_active=True).select_related(
                "created_by"
            ),
            "shares": record.shares.select_related("created_by"),
            "can_write": _can_write(request),
        },
    )


@login_required
@require_clinic
@require_roles(*WRITE_ROLES)
def record_edit(request, pk):
    patient = get_object_or_404(Patient.all_objects, clinic=request.clinic, pk=pk)
    record = services.ensure_record(patient)
    form = MedicalRecordForm(request.POST or None, instance=record)
    if request.method == "POST" and form.is_valid():
        record = form.save()
        record.touch_review(getattr(request, "clinic_membership", None))
        messages.success(request, _("Dossier médical mis à jour."))
        return redirect(reverse("web:medical-record", args=[patient.pk]))

    return render(
        request,
        "medical_records/record_form.html",
        {
            "form": form,
            "patient": patient,
            "record": record,
            "cancel_url": reverse("web:medical-record", args=[patient.pk]),
        },
    )


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------


@login_required
@require_clinic
@require_roles(*WRITE_ROLES)
def document_add(request, pk):
    patient = get_object_or_404(Patient.all_objects, clinic=request.clinic, pk=pk)
    record = services.ensure_record(patient)
    form = MedicalDocumentForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        document = form.save(commit=False)
        document.record = record
        document.clinic = patient.clinic
        document.created_by = request.user
        try:
            document.full_clean()
            document.save()
        except ValidationError as exc:
            form.add_error(None, exc.messages[0])
        else:
            messages.success(
                request, _("Document « %(title)s » ajouté.") % {"title": document.title}
            )
            return redirect(reverse("web:medical-record", args=[patient.pk]))

    return render(
        request,
        "medical_records/document_form.html",
        {
            "form": form,
            "patient": patient,
            "record": record,
            "cancel_url": reverse("web:medical-record", args=[patient.pk]),
        },
    )


@login_required
@require_clinic
@require_roles(*WRITE_ROLES)
def document_delete(request, pk):
    document = get_object_or_404(
        MedicalDocument.objects.filter(clinic=request.clinic).select_related("record"),
        pk=pk,
    )
    patient_id = document.record.patient_id
    if request.method == "POST":
        document.archive(_("Retiré du dossier (démo)."))
        messages.warning(request, _("Document retiré du dossier."))
        return redirect(reverse("web:medical-record", args=[patient_id]))

    return render(
        request,
        "medical_records/document_confirm_delete.html",
        {"document": document, "patient_id": patient_id},
    )


# ---------------------------------------------------------------------------
# Partages
# ---------------------------------------------------------------------------


@login_required
@require_clinic
@require_roles(*WRITE_ROLES)
def share_create(request, pk):
    patient = get_object_or_404(Patient.all_objects, clinic=request.clinic, pk=pk)
    record = services.ensure_record(patient)
    form = MedicalRecordShareForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            share = services.create_share(
                record,
                days=form.cleaned_data["days"],
                shared_with=form.cleaned_data.get("shared_with", ""),
                note=form.cleaned_data.get("note", ""),
                user=request.user,
            )
        except ValidationError as exc:
            form.add_error(None, exc.messages[0])
        else:
            messages.success(
                request,
                _("Lien de partage créé pour %(who)s.")
                % {"who": share.shared_with or patient.display_name},
            )
            return redirect(reverse("web:medical-record", args=[patient.pk]))

    return render(
        request,
        "medical_records/share_form.html",
        {
            "form": form,
            "patient": patient,
            "record": record,
            "cancel_url": reverse("web:medical-record", args=[patient.pk]),
            "max_days": share_max_days(),
        },
    )


@login_required
@require_clinic
@require_roles(*WRITE_ROLES)
def share_revoke(request, pk):
    share = get_object_or_404(
        MedicalRecordShare.objects.filter(clinic=request.clinic).select_related("record"),
        pk=pk,
    )
    patient_id = share.record.patient_id
    if request.method == "POST":
        services.revoke_share(share, user=request.user)
        messages.warning(request, _("Partage révoqué."))
        return redirect(reverse("web:medical-record", args=[patient_id]))

    return render(
        request,
        "medical_records/share_confirm_revoke.html",
        {"share": share, "patient_id": patient_id},
    )


# ---------------------------------------------------------------------------
# Accès public via jeton
# ---------------------------------------------------------------------------


def shared_record(request, token):
    """Consultation en lecture seule d'un dossier via un lien de partage valide."""
    share = (
        MedicalRecordShare.all_objects.filter(token=token)
        .select_related("record", "record__patient", "record__clinic")
        .first()
    )
    if share is None or not share.is_valid:
        raise Http404(_("Ce lien de partage est introuvable ou expiré."))
    record = share.record
    return render(
        request,
        "medical_records/shared_record.html",
        {
            "share": share,
            "record": record,
            "patient": record.patient,
            "clinic": record.patient.clinic,
            "documents": record.documents.filter(is_active=True),
        },
    )


# ---------------------------------------------------------------------------
# Aides
# ---------------------------------------------------------------------------


def _can_write(request) -> bool:
    membership = getattr(request, "clinic_membership", None)
    if membership is None:
        return False
    if request.user.is_platform_staff:
        return True
    return membership.role in WRITE_ROLES


def share_max_days() -> int:
    from django.conf import settings

    return getattr(settings, "RECORD_SHARE_MAX_DAYS", 30)
