"""Dossiers médicaux : antécédents, documents et partages (jalon 4).

Le dossier médical prolonge le dossier patient (jalon 2) : il regroupe les
antécédents structurés, les documents numérisés et les liens de partage à durée
limitée. Une consultation (jalon 4) alimente le dossier via le patient.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.accounts.models import Membership
from apps.common.models import BaseModel, CommentMixin
from apps.common.numbers import next_sequence
from apps.common.validators import validate_document_file
from apps.patients.models import Patient


def document_upload_path(instance, filename):
    """Chemin de stockage des pièces jointes, unique par fichier."""
    import os

    extension = os.path.splitext(filename)[1].lower()
    record_id = instance.record_id or "nouveau"
    return f"medical_records/{record_id}/{uuid.uuid4().hex}{extension}"


class MedicalRecord(BaseModel, CommentMixin):
    """Dossier médical d'un patient : un par patient, unique par clinique."""

    reference = models.CharField(
        _("référence"),
        max_length=32,
        blank=True,
        editable=False,
        help_text=_("Numéro attribué automatiquement (DM-AAAA-00001)."),
    )
    patient = models.OneToOneField(
        Patient,
        verbose_name=_("patient"),
        on_delete=models.CASCADE,
        related_name="medical_record",
    )

    # -- Antécédents structurés
    family_history = models.TextField(_("antécédents familiaux"), blank=True)
    medical_history = models.TextField(_("antécédents médicaux"), blank=True)
    surgical_history = models.TextField(_("antécédents chirurgicaux"), blank=True)
    gynecological_history = models.TextField(
        _("antécédents gynéco-obstétriques"), blank=True
    )
    vaccinations = models.TextField(_("vaccinations"), blank=True)
    lifestyle = models.TextField(
        _("mode de vie"),
        blank=True,
        help_text=_("Tabac, alcool, activité physique, régime…"),
    )
    disabilities = models.TextField(_("handicaps et particularités"), blank=True)

    last_reviewed_at = models.DateTimeField(_("dernière revue"), null=True, blank=True)
    reviewed_by = models.ForeignKey(
        Membership,
        verbose_name=_("revu par"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reviewed_records",
    )

    class Meta:
        verbose_name = _("dossier médical")
        verbose_name_plural = _("dossiers médicaux")
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["clinic", "reference"]),
            models.Index(fields=["patient"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["clinic", "reference"],
                name="uniq_medical_record_reference_per_clinic",
            )
        ]

    def __str__(self):
        return f"{self.reference or '—'} — {self.patient}"

    # -- Persistance ------------------------------------------------------

    def save(self, *args, **kwargs):
        if self.patient_id and self.patient.clinic_id:
            if self.clinic_id and self.clinic_id != self.patient.clinic_id:
                raise ValidationError(_("Le patient appartient à une autre clinique."))
            if not self.clinic_id:
                self.clinic = self.patient.clinic
        if not self.reference:
            self.reference = next_sequence(self.clinic, "medical_record", "DM", digits=5)
        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        if (
            self.patient_id
            and self.patient.clinic_id
            and self.clinic_id
            and self.patient.clinic_id != self.clinic_id
        ):
            raise ValidationError(_("Le patient appartient à une autre clinique."))

    # -- Accès ------------------------------------------------------------

    @classmethod
    def for_patient(cls, patient: Patient) -> MedicalRecord:
        """Renvoie le dossier du patient, créé à la volée si nécessaire."""
        record = cls.all_objects.filter(patient=patient).first()
        if record is None:
            record = cls(patient=patient, clinic=patient.clinic)
            record.full_clean()
            record.save()
        elif not record.is_active:
            record.restore()
        return record

    # -- Affichage --------------------------------------------------------

    @property
    def document_count(self) -> int:
        return self.documents.filter(is_active=True).count()

    @property
    def active_share(self):
        return (
            self.shares.filter(is_active=True, revoked_at__isnull=True)
            .filter(expires_at__gt=timezone.now())
            .order_by("-created_at")
            .first()
        )

    @property
    def has_active_share(self) -> bool:
        return self.active_share is not None

    def touch_review(self, membership=None) -> None:
        """Marque le dossier comme revu aujourd'hui."""
        self.last_reviewed_at = timezone.now()
        if membership is not None:
            self.reviewed_by = membership
        self.save(update_fields=["last_reviewed_at", "reviewed_by", "updated_at"])


class MedicalDocument(BaseModel):
    """Pièce du dossier médical : ordonnance scannée, compte rendu, imagerie…"""

    class Category(models.TextChoices):
        PRESCRIPTION = "PRESCRIPTION", _("Ordonnance")
        LAB = "LAB", _("Analyse / laboratoire")
        IMAGING = "IMAGING", _("Imagerie")
        REPORT = "REPORT", _("Compte rendu")
        HOSPITAL = "HOSPITAL", _("Hospitalisation")
        INSURANCE = "INSURANCE", _("Assurance / mutuelle")
        CONSENT = "CONSENT", _("Consentement")
        OTHER = "OTHER", _("Autre")

    record = models.ForeignKey(
        MedicalRecord,
        verbose_name=_("dossier médical"),
        on_delete=models.CASCADE,
        related_name="documents",
    )
    category = models.CharField(
        _("catégorie"),
        max_length=16,
        choices=Category.choices,
        default=Category.OTHER,
    )
    title = models.CharField(_("intitulé"), max_length=160)
    file = models.FileField(
        _("fichier"),
        upload_to=document_upload_path,
        validators=[validate_document_file],
    )
    document_date = models.DateField(_("date du document"), null=True, blank=True)
    description = models.TextField(_("description"), blank=True)

    class Meta:
        verbose_name = _("document médical")
        verbose_name_plural = _("documents médicaux")
        ordering = ["-document_date", "-id"]
        indexes = [
            models.Index(fields=["clinic", "category"]),
            models.Index(fields=["record", "document_date"]),
        ]

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if self.record_id and not self.clinic_id:
            self.clinic = self.record.patient.clinic
        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        if (
            self.record_id
            and self.document_date
            and self.document_date > timezone.localdate()
        ):
            raise ValidationError({"document_date": _("La date du document est future.")})

    @property
    def filename(self) -> str:
        import os

        return os.path.basename(self.file.name) if self.file else ""

    @property
    def extension(self) -> str:
        name = self.file.name if self.file else ""
        return name.rsplit(".", 1)[-1].lower() if "." in name else ""

    @property
    def category_css(self) -> str:
        return "badge--muted"


class MedicalRecordShare(BaseModel):
    """Lien de partage temporaire du dossier, à durée limitée."""

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", _("Actif")
        EXPIRED = "EXPIRED", _("Expiré")
        REVOKED = "REVOKED", _("Révoqué")

    record = models.ForeignKey(
        MedicalRecord,
        verbose_name=_("dossier médical"),
        on_delete=models.CASCADE,
        related_name="shares",
    )
    token = models.UUIDField(_("jeton"), default=uuid.uuid4, unique=True, editable=False)
    shared_with = models.CharField(
        _("destinataire"),
        max_length=160,
        blank=True,
        help_text=_("Médecin traitant, confrère, patient…"),
    )
    note = models.TextField(_("note"), blank=True)
    expires_at = models.DateTimeField(_("expire le"))
    revoked_at = models.DateTimeField(_("révoqué le"), null=True, blank=True)

    class Meta:
        verbose_name = _("partage de dossier")
        verbose_name_plural = _("partages de dossier")
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.record} → {self.shared_with or self.get_status_display()}"

    def save(self, *args, **kwargs):
        if self.record_id and not self.clinic_id:
            self.clinic = self.record.patient.clinic
        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        max_days = getattr(settings, "RECORD_SHARE_MAX_DAYS", 30)
        if self.expires_at and self.expires_at > timezone.now() + timedelta(
            days=max_days
        ):
            raise ValidationError(
                {
                    "expires_at": _("Un partage ne peut pas dépasser %(days)s jours.")
                    % {"days": max_days}
                }
            )

    # -- Cycle de vie -----------------------------------------------------

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None

    @property
    def is_expired(self) -> bool:
        return self.expires_at is not None and self.expires_at <= timezone.now()

    @property
    def is_valid(self) -> bool:
        return self.is_active and not self.is_revoked and not self.is_expired

    @property
    def status(self) -> str:
        if self.is_revoked:
            return self.Status.REVOKED
        if self.is_expired:
            return self.Status.EXPIRED
        return self.Status.ACTIVE

    @property
    def status_label(self) -> str:
        return self.Status(self.status).label

    @property
    def status_css(self) -> str:
        return {
            self.Status.ACTIVE: "badge--ok",
            self.Status.EXPIRED: "badge--muted",
            self.Status.REVOKED: "badge--cancelled",
        }.get(self.status, "badge--muted")

    def revoke(self, *, user=None) -> MedicalRecordShare:
        if self.revoked_at is None:
            self.revoked_at = timezone.now()
            self.save(update_fields=["revoked_at", "updated_at"])
        return self
