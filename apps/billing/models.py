"""Facturation (jalon 6) : factures, lignes, règlements, impayés.

Une facture rattache un patient (et, le cas échéant, la consultation qui a
motivé l'acte) à des lignes prestations. Les montants sont calculés à la
lecture (propriétés) à partir des lignes et du taux de TVA de la facture ;
le montant encaissé (``paid_amount``) est dénormalisé et tenu à jour par les
règlements — voir ``apps.billing.services`` pour les règles :

- référence ``FAC-AAAA-00001`` par clinique ;
- cycle : brouillon → émise → (partiellement payée) → payée ; annulation
  possible uniquement sans aucun règlement ;
- un règlement ne peut pas dépasser le reste à payer ;
- ``due_at`` (échéance) alimente la liste des impayés et les relances.
"""

from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.common.models import BaseModel, CommentMixin
from apps.common.numbers import next_sequence


class Invoice(BaseModel, CommentMixin):
    """Facture d'une clinique, rattachée à un dossier patient."""

    class Status(models.TextChoices):
        DRAFT = "DRAFT", _("Brouillon")
        ISSUED = "ISSUED", _("Émise")
        PARTIAL = "PARTIAL", _("Partiellement payée")
        PAID = "PAID", _("Payée")
        CANCELLED = "CANCELLED", _("Annulée")

    #: Transitions autorisées : une facture payée est définitive ; une facture
    #: avec règlement ne peut plus être annulée (remboursement hors périmètre).
    ALLOWED_TRANSITIONS = {
        Status.DRAFT: {Status.ISSUED, Status.CANCELLED},
        Status.ISSUED: {Status.PARTIAL, Status.PAID, Status.CANCELLED},
        Status.PARTIAL: {Status.PAID},
        Status.PAID: set(),
        Status.CANCELLED: set(),
    }

    reference = models.CharField(
        _("référence"),
        max_length=32,
        blank=True,
        editable=False,
        help_text=_("Numéro de facture attribué automatiquement (FAC-AAAA-00001)."),
    )
    patient = models.ForeignKey(
        "patients.Patient",
        verbose_name=_("patient"),
        on_delete=models.CASCADE,
        related_name="invoices",
    )
    consultation = models.ForeignKey(
        "consultations.Consultation",
        verbose_name=_("consultation"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="invoices",
        help_text=_("Acte médical à l'origine de la facture, s'il y a lieu."),
    )
    status = models.CharField(
        _("état"), max_length=12, choices=Status.choices, default=Status.DRAFT
    )
    issued_at = models.DateTimeField(_("émise le"), null=True, blank=True)
    due_at = models.DateField(_("échéance"), null=True, blank=True)
    tax_rate = models.DecimalField(
        _("taux de TVA"),
        max_digits=5,
        decimal_places=4,
        default=Decimal("0"),
        help_text=_("Ex. : 0.2000 pour 20 % ; 0 pour exonéré."),
    )
    paid_amount = models.DecimalField(
        _("montant encaissé"),
        max_digits=10,
        decimal_places=2,
        default=Decimal("0"),
        editable=False,
    )
    paid_at = models.DateTimeField(_("soldée le"), null=True, blank=True)
    cancelled_at = models.DateTimeField(_("annulée le"), null=True, blank=True)
    cancellation_reason = models.CharField(
        _("motif d'annulation"), max_length=255, blank=True
    )

    class Meta:
        verbose_name = _("facture")
        verbose_name_plural = _("factures")
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["clinic", "status"]),
            models.Index(fields=["clinic", "due_at"]),
            models.Index(fields=["patient", "-created_at"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["clinic", "reference"],
                name="uniq_invoice_reference_per_clinic",
            ),
        ]

    def __str__(self):
        return f"{self.reference or '—'} — {self.patient} ({self.get_status_display()})"

    # -- Persistence ------------------------------------------------------

    def save(self, *args, **kwargs):
        if self.patient_id and self.patient.clinic_id:
            if self.clinic_id and self.clinic_id != self.patient.clinic_id:
                raise ValidationError(_("Le patient appartient à une autre clinique."))
            if not self.clinic_id:
                self.clinic = self.patient.clinic
        if not self.reference:
            self.reference = next_sequence(self.clinic, "invoice", "FAC", digits=5)
        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        errors = {}
        if self.tax_rate is not None and (self.tax_rate < 0 or self.tax_rate > 1):
            errors["tax_rate"] = _("Le taux de TVA est un ratio entre 0 et 1.")
        if self.due_at and self.issued_at and self.due_at < self.issued_at.date():
            errors["due_at"] = _("L'échéance précède l'émission.")
        if errors:
            raise ValidationError(errors)

    # -- Transitions ------------------------------------------------------

    def can_transition_to(self, status: str) -> bool:
        return status in self.ALLOWED_TRANSITIONS.get(self.status, set())

    def transition_to(self, status: str, *, reason: str = "", user=None) -> None:
        """Change l'état de la facture en journalisant l'annulation éventuelle."""
        if status not in self.Status.values:
            raise ValidationError(_("État de facture inconnu."))
        if status == self.status:
            return
        if not self.can_transition_to(status):
            labels = dict(self.Status.choices)
            raise ValidationError(
                _("Transition impossible : %(current)s → %(target)s."),
                code="invalid_transition",
                params={
                    "current": labels.get(self.status, self.status),
                    "target": labels.get(status, status),
                },
            )
        self.status = status
        fields = ["status", "updated_at"]
        if status == self.Status.ISSUED:
            self.issued_at = timezone.now()
            fields.append("issued_at")
        elif status == self.Status.PAID:
            self.paid_at = timezone.now()
            fields.append("paid_at")
        elif status == self.Status.CANCELLED:
            self.cancelled_at = timezone.now()
            self.cancellation_reason = (reason or "").strip()[:255]
            fields += ["cancelled_at", "cancellation_reason"]
        self.save(update_fields=fields)

    # -- Montants (calculés à la lecture) ---------------------------------

    @property
    def amount_excl_tax(self) -> Decimal:
        return sum((line.line_total for line in self.lines.all()), Decimal("0"))

    @property
    def tax_amount(self) -> Decimal:
        return (self.amount_excl_tax * (self.tax_rate or Decimal("0"))).quantize(
            Decimal("0.01")
        )

    @property
    def total(self) -> Decimal:
        return self.amount_excl_tax + self.tax_amount

    @property
    def amount_due(self) -> Decimal:
        return max(self.total - (self.paid_amount or Decimal("0")), Decimal("0"))

    # -- État -------------------------------------------------------------

    @property
    def is_cancelled(self) -> bool:
        return self.status == self.Status.CANCELLED

    @property
    def is_settled(self) -> bool:
        return self.status == self.Status.PAID

    @property
    def is_overdue(self) -> bool:
        """Émise et impayée au-delà de l'échéance."""
        return (
            self.status in {self.Status.ISSUED, self.Status.PARTIAL}
            and self.due_at is not None
            and self.due_at < timezone.localdate()
        )

    @property
    def overdue_days(self) -> int:
        if not self.is_overdue:
            return 0
        return (timezone.localdate() - self.due_at).days

    @property
    def display_patient(self) -> str:
        return self.patient.display_name if self.patient_id else "—"

    @property
    def status_css(self) -> str:
        return f"badge--{self.status.lower()}"


class InvoiceLine(models.Model):
    """Ligne de prestation d'une facture (acte, produit, forfait…)."""

    invoice = models.ForeignKey(
        Invoice,
        verbose_name=_("facture"),
        on_delete=models.CASCADE,
        related_name="lines",
    )
    label = models.CharField(_("prestation"), max_length=160)
    quantity = models.DecimalField(
        _("quantité"), max_digits=6, decimal_places=2, default=Decimal("1")
    )
    unit_price = models.DecimalField(_("prix unitaire"), max_digits=10, decimal_places=2)

    class Meta:
        verbose_name = _("ligne de facture")
        verbose_name_plural = _("lignes de facture")
        ordering = ["pk"]

    def __str__(self):
        return f"{self.label} × {self.quantity}"

    @property
    def line_total(self) -> Decimal:
        return (self.quantity * self.unit_price).quantize(Decimal("0.01"))


class Payment(BaseModel):
    """Règlement partiel ou total d'une facture."""

    class Method(models.TextChoices):
        CASH = "CASH", _("Espèces")
        CARD = "CARD", _("Carte bancaire")
        TRANSFER = "TRANSFER", _("Virement")
        CHECK = "CHECK", _("Chèque")
        MOBILE = "MOBILE", _("Paiement mobile")
        INSURANCE = "INSURANCE", _("Assurance / mutuelle")

    invoice = models.ForeignKey(
        Invoice,
        verbose_name=_("facture"),
        on_delete=models.CASCADE,
        related_name="payments",
    )
    amount = models.DecimalField(_("montant"), max_digits=10, decimal_places=2)
    method = models.CharField(
        _("moyen de paiement"), max_length=12, choices=Method.choices
    )
    paid_at = models.DateTimeField(_("payé le"), default=timezone.now)
    reference = models.CharField(
        _("référence"),
        max_length=60,
        blank=True,
        help_text=_("N° de chèque, de transaction ou de bordereau."),
    )

    class Meta:
        verbose_name = _("règlement")
        verbose_name_plural = _("règlements")
        ordering = ["-paid_at", "-created_at"]

    def __str__(self):
        return f"{self.invoice.reference} — {self.amount} ({self.get_method_display()})"
