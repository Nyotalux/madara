"""Règles de gestion du dossier médical (partages à durée limitée, revue)."""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.patients.models import Patient

from .models import MedicalDocument, MedicalRecord, MedicalRecordShare

DATE_ERROR = _("La date du document est future.")


def ensure_record(patient: Patient) -> MedicalRecord:
    """Récupère (ou crée) le dossier médical d'un patient."""
    return MedicalRecord.for_patient(patient)


@transaction.atomic
def add_document(
    record: MedicalRecord,
    *,
    category: str,
    title: str,
    file,
    document_date=None,
    description: str = "",
    user=None,
) -> MedicalDocument:
    """Ajoute une pièce au dossier, après contrôle du fichier."""
    if document_date is not None and document_date > timezone.localdate():
        raise ValidationError({"document_date": DATE_ERROR})
    document = MedicalDocument(
        clinic=record.patient.clinic,
        record=record,
        category=category,
        title=title,
        file=file,
        document_date=document_date,
        description=description,
        created_by=user if getattr(user, "is_authenticated", False) else None,
    )
    document.full_clean()
    document.save()
    return document


@transaction.atomic
def create_share(
    record: MedicalRecord,
    *,
    days: int | None = None,
    shared_with: str = "",
    note: str = "",
    user=None,
) -> MedicalRecordShare:
    """Crée un lien de partage, dont la durée est bornée par les réglages."""
    max_days = getattr(settings, "RECORD_SHARE_MAX_DAYS", 30)
    days = days or min(7, max_days)
    if days < 1:
        raise ValidationError(_("La durée du partage doit être d'au moins un jour."))
    if days > max_days:
        raise ValidationError(
            _("Un partage ne peut pas dépasser %(days)s jours.") % {"days": max_days}
        )

    share = MedicalRecordShare(
        clinic=record.patient.clinic,
        record=record,
        shared_with=shared_with.strip(),
        note=note.strip(),
        expires_at=timezone.now() + timedelta(days=days),
        created_by=user if getattr(user, "is_authenticated", False) else None,
    )
    share.full_clean()
    share.save()
    return share


@transaction.atomic
def revoke_share(share: MedicalRecordShare, *, user=None) -> MedicalRecordShare:
    """Révoque un partage : le lien devient immédiatement inutilisable."""
    return share.revoke(user=user)
