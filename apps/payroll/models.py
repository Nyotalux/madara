"""Salaires et primes (jalon 6).

Le salaire de base de chacun vit sur ``Membership.base_salary`` (géré depuis
l'écran « Personnel »). La paie d'une période (mois ``AAAA-MM``) rassemble :

- les ``Bonus`` : primes et retenues ponctuelles de la période ;
- un ``Payslip`` par membre du personnel : bulletin basé sur le salaire de
  base de la période, majoré des primes et minoré des retenues.

Les règles vivent dans ``apps.payroll.services`` :

- référence ``BS-AAAA-00001`` par clinique (bulletin de salaire) ;
- cycle : brouillon → payé ; annulation possible tant que le bulletin est
  impayé (motif journalisé) ; un bulletin payé est définitif ;
- un seul bulletin par membre du personnel et par période ;
- la génération d'une période reprend salaire de base, primes et retenues.
"""

from __future__ import annotations

import re
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.common.models import BaseModel
from apps.common.numbers import next_sequence

_PERIOD_RE = re.compile(r"\d{4}-\d{2}")


class Bonus(BaseModel):
    """Prime ou retenue ponctuelle d'un membre du personnel pour une période."""

    class Kind(models.TextChoices):
        BONUS = "BONUS", _("Prime")
        DEDUCTION = "DEDUCTION", _("Retenue")

    membership = models.ForeignKey(
        "accounts.Membership",
        verbose_name=_("membre du personnel"),
        on_delete=models.CASCADE,
        related_name="bonuses",
    )
    period = models.CharField(_("période"), max_length=7)
    kind = models.CharField(_("type"), max_length=10, choices=Kind.choices)
    label = models.CharField(_("libellé"), max_length=160)
    amount = models.DecimalField(
        _("montant"),
        max_digits=10,
        decimal_places=2,
        help_text=_("Toujours positif ; le sens est porté par le type."),
    )

    class Meta:
        verbose_name = _("prime ou retenue")
        verbose_name_plural = _("primes et retenues")
        ordering = ["-period", "created_at"]
        indexes = [
            models.Index(fields=["clinic", "period"]),
            models.Index(fields=["membership", "-period"]),
        ]

    def __str__(self):
        sign = {"BONUS": "+", "DEDUCTION": "−"}.get(self.kind, "")
        return f"{self.membership} {self.period} {sign}{self.amount} ({self.label})"

    # -- Persistence ------------------------------------------------------

    def save(self, *args, **kwargs):
        if self.membership_id and self.membership.clinic_id:
            if self.clinic_id and self.clinic_id != self.membership.clinic_id:
                raise ValidationError(
                    _("Le membre du personnel appartient à une autre clinique.")
                )
            if not self.clinic_id:
                self.clinic = self.membership.clinic
        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        errors = {}
        if not _PERIOD_RE.fullmatch(self.period or ""):
            errors["period"] = _("La période suit le format AAAA-MM.")
        if not (self.label or "").strip():
            errors["label"] = _("Le libellé est obligatoire.")
        if self.amount is not None and self.amount <= 0:
            errors["amount"] = _("Le montant doit être strictement positif.")
        if errors:
            raise ValidationError(errors)

    @property
    def signed_amount(self) -> Decimal:
        if self.kind == self.Kind.DEDUCTION:
            return -(self.amount or Decimal("0"))
        return self.amount or Decimal("0")


class Payslip(BaseModel):
    """Bulletin de salaire d'un membre du personnel pour une période."""

    class Status(models.TextChoices):
        DRAFT = "DRAFT", _("Brouillon")
        PAID = "PAID", _("Payé")
        CANCELLED = "CANCELLED", _("Annulé")

    ALLOWED_TRANSITIONS = {
        Status.DRAFT: {Status.PAID, Status.CANCELLED},
        Status.PAID: set(),
        Status.CANCELLED: set(),
    }

    reference = models.CharField(
        _("référence"),
        max_length=32,
        blank=True,
        editable=False,
        help_text=_("Numéro de bulletin attribué automatiquement (BS-AAAA-00001)."),
    )
    membership = models.ForeignKey(
        "accounts.Membership",
        verbose_name=_("membre du personnel"),
        on_delete=models.CASCADE,
        related_name="payslips",
    )
    period = models.CharField(_("période"), max_length=7)
    status = models.CharField(
        _("état"), max_length=12, choices=Status.choices, default=Status.DRAFT
    )
    base_amount = models.DecimalField(
        _("salaire de base"), max_digits=10, decimal_places=2, default=Decimal("0")
    )
    bonus_amount = models.DecimalField(
        _("primes"), max_digits=10, decimal_places=2, default=Decimal("0")
    )
    deduction_amount = models.DecimalField(
        _("retenues"), max_digits=10, decimal_places=2, default=Decimal("0")
    )
    paid_at = models.DateTimeField(_("payé le"), null=True, blank=True)
    cancelled_at = models.DateTimeField(_("annulé le"), null=True, blank=True)
    cancellation_reason = models.CharField(
        _("motif d'annulation"), max_length=255, blank=True
    )
    notes = models.TextField(_("notes"), blank=True, default="")

    class Meta:
        verbose_name = _("bulletin de salaire")
        verbose_name_plural = _("bulletins de salaire")
        ordering = ["-period", "created_at"]
        indexes = [
            models.Index(fields=["clinic", "period"]),
            models.Index(fields=["clinic", "status"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["clinic", "reference"],
                name="uniq_payslip_reference_per_clinic",
            ),
            models.UniqueConstraint(
                fields=["clinic", "membership", "period"],
                name="uniq_payslip_membership_period_per_clinic",
            ),
        ]

    def __str__(self):
        return f"{self.reference or '—'} — {self.membership} ({self.period})"

    # -- Persistence ------------------------------------------------------

    def save(self, *args, **kwargs):
        if self.membership_id and self.membership.clinic_id:
            if self.clinic_id and self.clinic_id != self.membership.clinic_id:
                raise ValidationError(
                    _("Le membre du personnel appartient à une autre clinique.")
                )
            if not self.clinic_id:
                self.clinic = self.membership.clinic
        if not self.reference:
            self.reference = next_sequence(self.clinic, "payslip", "BS", digits=5)
        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        errors = {}
        if not _PERIOD_RE.fullmatch(self.period or ""):
            errors["period"] = _("La période suit le format AAAA-MM.")
        for field in ("base_amount", "bonus_amount", "deduction_amount"):
            value = getattr(self, field, None)
            if value is not None and value < 0:
                errors[field] = _("Le montant ne peut pas être négatif.")
        if errors:
            raise ValidationError(errors)

    # -- Transitions ------------------------------------------------------

    def can_transition_to(self, status: str) -> bool:
        return status in self.ALLOWED_TRANSITIONS.get(self.status, set())

    def transition_to(self, status: str, *, reason: str = "", user=None) -> None:
        if status not in self.Status.values:
            raise ValidationError(_("État de bulletin inconnu."))
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
        if status == self.Status.PAID:
            self.paid_at = timezone.now()
            fields.append("paid_at")
        elif status == self.Status.CANCELLED:
            self.cancelled_at = timezone.now()
            self.cancellation_reason = (reason or "").strip()[:255]
            fields += ["cancelled_at", "cancellation_reason"]
        self.save(update_fields=fields)

    # -- Montants ---------------------------------------------------------

    @property
    def net_amount(self) -> Decimal:
        base = self.base_amount or Decimal("0")
        bonus = self.bonus_amount or Decimal("0")
        deduction = self.deduction_amount or Decimal("0")
        return max(base + bonus - deduction, Decimal("0"))

    # -- État -------------------------------------------------------------

    @property
    def is_paid(self) -> bool:
        return self.status == self.Status.PAID

    @property
    def display_staff(self) -> str:
        return str(self.membership)

    @property
    def status_css(self) -> str:
        return f"badge--{self.status.lower()}"
