"""Règles de paie : bulletins, primes, paiements (jalon 6).

Fonctions module-level comme dans les autres apps : les vues web et l'API
passent toutes par ici. La paie est réservée à l'administration de la
clinique et à la comptabilité : un bulletin est confidentiel et immuable
une fois payé.
"""

from __future__ import annotations

import re
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .models import Bonus, Payslip

#: Rôles habilités à consulter et traiter la paie.
PAYROLL_ROLES = ("ADMIN", "ACCOUNTANT")

_PERIOD_RE = re.compile(r"\d{4}-\d{2}")


def normalize_period(value: str) -> str:
    """Nettoie et valide ``2026-10`` ; sinon ``ValidationError``."""
    period = (str(value or "")).strip()[:7]
    if not _PERIOD_RE.fullmatch(period):
        raise ValidationError({"period": _("La période suit le format AAAA-MM.")})
    month = int(period[5:])
    if not 1 <= month <= 12:
        raise ValidationError({"period": _("La période suit le format AAAA-MM.")})
    return period


def current_period() -> str:
    """Période du mois en cours : ``2026-10``."""
    return timezone.localdate().strftime("%Y-%m")


@transaction.atomic
def add_bonus(
    clinic,
    membership,
    *,
    kind: str,
    label: str,
    amount,
    period: str | None = None,
    user=None,
) -> Bonus:
    """Ajoute une prime ou une retenue ponctuelle pour la période donnée."""
    if membership.clinic_id != clinic.pk:
        raise ValidationError(
            _("Le membre du personnel appartient à une autre clinique.")
        )
    if kind not in Bonus.Kind.values:
        raise ValidationError({"kind": _("Type de prime inconnu.")})
    period = normalize_period(period) if period else current_period()

    bonus = Bonus(
        clinic=clinic,
        membership=membership,
        period=period,
        kind=kind,
        label=(label or "").strip()[:160],
        amount=Decimal(str(amount or 0)).quantize(Decimal("0.01")),
        created_by=user if getattr(user, "is_authenticated", False) else None,
    )
    bonus.full_clean()
    bonus.save()
    return bonus


def remove_bonus(bonus: Bonus, *, user=None) -> None:
    """Retire une prime / retenue non encore versée dans un bulletin payé."""
    payslip = Payslip.objects.filter(
        membership_id=bonus.membership_id, period=bonus.period
    ).first()
    if payslip is not None and payslip.status == Payslip.Status.PAID:
        raise ValidationError(
            _("Le bulletin de la période est payé : la prime ne peut plus être retirée.")
        )
    bonus.delete()


def period_summary(clinic, period: str) -> dict:
    """Compteur d'une période : bulletins, à payer, payés, masse salariale."""
    period = normalize_period(period)
    payslips = Payslip.objects.filter(clinic=clinic, period=period).select_related(
        "membership__user"
    )
    return {
        "period": period,
        "total": payslips.exclude(status=Payslip.Status.CANCELLED).count(),
        "drafts": payslips.filter(status=Payslip.Status.DRAFT).count(),
        "paid": payslips.filter(status=Payslip.Status.PAID).count(),
        "mass_payroll": sum(
            payslip.net_amount
            for payslip in payslips.exclude(status=Payslip.Status.CANCELLED)
        ),
        "paid_total": sum(
            payslip.net_amount for payslip in payslips.filter(status=Payslip.Status.PAID)
        ),
        "pending_total": sum(
            payslip.net_amount for payslip in payslips.filter(status=Payslip.Status.DRAFT)
        ),
    }


def latest_periods(clinic, count: int = 12):
    """Périodes représentées dans la clinique, à la plus récente."""
    return (
        Payslip.objects.filter(clinic=clinic)
        .order_by("-period")
        .values_list("period", flat=True)
        .distinct()[:count]
    )


@transaction.atomic
def generate_payslips(clinic, *, period: str, user=None) -> list[Payslip]:
    """Établit les bulletins manquants de la période depuis le salaire de base.

    Pour chaque membership actif de la clinique : salaire de base repris de
    ``membership.base_salary``, primes et retenues additionnées depuis les
    ``Bonus`` de la période. Les bulletins existants (même brouillons) ne
    sont pas reproduits ; relancer l'opération n'écrase donc rien.
    """
    period = normalize_period(period)
    created = []
    memberships = clinic.memberships.filter(is_active=True).select_related("user")

    for membership in memberships:
        existing = Payslip.objects.filter(
            clinic=clinic, membership=membership, period=period
        ).first()
        if existing is not None:
            continue
        bonuses = membership.bonuses.filter(clinic=clinic, period=period)
        payslip = Payslip(
            clinic=clinic,
            membership=membership,
            period=period,
            status=Payslip.Status.DRAFT,
            base_amount=(membership.base_salary or Decimal("0")).quantize(
                Decimal("0.01")
            ),
            bonus_amount=sum(
                (bonus.amount for bonus in bonuses if bonus.kind == Bonus.Kind.BONUS),
                Decimal("0"),
            ).quantize(Decimal("0.01")),
            deduction_amount=sum(
                (bonus.amount for bonus in bonuses if bonus.kind == Bonus.Kind.DEDUCTION),
                Decimal("0"),
            ).quantize(Decimal("0.01")),
            notes="",
            created_by=user if getattr(user, "is_authenticated", False) else None,
        )
        payslip.full_clean()
        payslip.save()
        created.append(payslip)
    return created


@transaction.atomic
def update_payslip(
    payslip: Payslip,
    *,
    base_amount=None,
    bonus_amount=None,
    deduction_amount=None,
    notes=None,
    user=None,
) -> Payslip:
    """Ajuste un bulletin brouillon (montants de la période)."""
    if payslip.status != Payslip.Status.DRAFT:
        raise ValidationError(
            _(
                "Seul un bulletin brouillon est modifiable : %(reference)s est déjà traité."
            )
            % {"reference": payslip.reference}
        )
    if base_amount is not None:
        value = Decimal(str(base_amount)).quantize(Decimal("0.01"))
        if value < 0:
            raise ValidationError(
                {"base_amount": _("Le montant ne peut pas être négatif.")}
            )
        payslip.base_amount = value
    if bonus_amount is not None:
        value = Decimal(str(bonus_amount)).quantize(Decimal("0.01"))
        if value < 0:
            raise ValidationError(
                {"bonus_amount": _("Le montant ne peut pas être négatif.")}
            )
        payslip.bonus_amount = value
    if deduction_amount is not None:
        value = Decimal(str(deduction_amount)).quantize(Decimal("0.01"))
        if value < 0:
            raise ValidationError(
                {"deduction_amount": _("Le montant ne peut pas être négatif.")}
            )
        payslip.deduction_amount = value
    if notes is not None:
        payslip.notes = notes
    payslip.updated_at = timezone.now()
    payslip.save(
        update_fields=[
            "base_amount",
            "bonus_amount",
            "deduction_amount",
            "notes",
            "updated_at",
        ]
    )
    return payslip


@transaction.atomic
def pay_payslip(payslip: Payslip, *, user=None, paid_at=None) -> Payslip:
    """Paie un bulletin : opération définitive."""
    payslip.transition_to(Payslip.Status.PAID, user=user)
    if paid_at is not None:
        payslip.paid_at = paid_at
        payslip.save(update_fields=["paid_at", "updated_at"])
    return payslip


@transaction.atomic
def cancel_payslip(payslip: Payslip, *, reason: str = "", user=None) -> Payslip:
    """Annule un bulletin impayé (erreur de saisie)."""
    payslip.transition_to(Payslip.Status.CANCELLED, reason=reason, user=user)
    return payslip
