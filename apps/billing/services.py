"""Règles de facturation : émission, règlements, impayés (jalon 6).

Fonctions module-level comme dans les autres apps : les vues web, l'API et les
tâches Celery passent toutes par ici. La clinique est toujours explicite et
les contrôles de montants se font sous verrou pessimiste sur la facture
(deux règlements simultanés ne peuvent pas dépasser le total).
"""

from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .models import Invoice, InvoiceLine, Payment

#: Rôles habilités à consulter la facturation (les praticiens y sont exclus).
BILLING_READ_ROLES = ("ADMIN", "RECEPTION", "ACCOUNTANT")
#: Rôles habilités à facturer et encaisser.
BILLING_WRITE_ROLES = ("ADMIN", "RECEPTION", "ACCOUNTANT")
#: Rôles habilités à annuler une facture (acte financier sensible).
BILLING_CANCEL_ROLES = ("ADMIN", "ACCOUNTANT")

#: Délai d'échéance par défaut d'une facture émise, en jours.
DEFAULT_DUE_DAYS = 30


@transaction.atomic
def create_invoice(
    *,
    clinic,
    patient,
    lines,
    consultation=None,
    tax_rate=None,
    due_days: int | None = None,
    status: str = Invoice.Status.DRAFT,
    notes: str = "",
    user=None,
) -> Invoice:
    """Crée une facture (brouillon ou directement émise) avec ses lignes.

    ``lines`` : liste de dictionnaires ``{"label", "quantity", "unit_price"}``.
    Les lignes vides (sans libellé ou à prix nul) sont ignorées ; une facture
    sans aucune ligne est refusée.
    """
    if patient.clinic_id != clinic.pk:
        raise ValidationError(_("Le patient n'appartient pas à cette clinique."))
    if consultation is not None and consultation.clinic_id != clinic.pk:
        raise ValidationError(_("La consultation n'appartient pas à cette clinique."))

    cleaned = [
        {
            "label": str(item.get("label") or "").strip()[:160],
            "quantity": Decimal(str(item.get("quantity") or 1)),
            "unit_price": Decimal(str(item.get("unit_price") or 0)).quantize(
                Decimal("0.01")
            ),
        }
        for item in lines or []
    ]
    cleaned = [item for item in cleaned if item["label"] and item["unit_price"] > 0]
    if not cleaned:
        raise ValidationError(
            {"lines": _("Ajoutez au moins une prestation avec un prix.")}
        )
    for item in cleaned:
        if item["quantity"] <= 0:
            raise ValidationError(
                {"lines": _("Les quantités doivent être strictement positives.")}
            )

    if status not in {Invoice.Status.DRAFT, Invoice.Status.ISSUED}:
        raise ValidationError(
            _("Une facture se crée en brouillon ou est émise directement.")
        )

    invoice = Invoice(
        clinic=clinic,
        patient=patient,
        consultation=consultation,
        tax_rate=(
            Decimal(str(tax_rate))
            if tax_rate is not None
            else (clinic.tax_rate or Decimal("0"))
        ),
        notes=notes or "",
        status=status,
        created_by=user if getattr(user, "is_authenticated", False) else None,
    )
    invoice.full_clean(exclude=["due_at"])
    invoice.save()
    InvoiceLine.objects.bulk_create(
        [InvoiceLine(invoice=invoice, **item) for item in cleaned]
    )

    if status == Invoice.Status.ISSUED:
        _issue(invoice, due_days=due_days)
    return invoice


def _issue(invoice: Invoice, *, due_days: int | None = None) -> None:
    """Émission : fixe l'échéance (facture déjà verrouillée par l'appelant)."""
    days = due_days or DEFAULT_DUE_DAYS
    invoice.due_at = timezone.localdate() + timezone.timedelta(days=days)
    invoice.status = Invoice.Status.ISSUED
    invoice.issued_at = timezone.now()
    invoice.save(update_fields=["due_at", "status", "issued_at", "updated_at"])


@transaction.atomic
def issue_invoice(invoice: Invoice, *, due_days: int | None = None, user=None) -> Invoice:
    """Émet un brouillon : les lignes ne sont plus modifiables, la facture part."""
    if invoice.lines.count() == 0:
        raise ValidationError(_("Impossible d'émettre une facture sans prestation."))
    invoice.transition_to(Invoice.Status.ISSUED, user=user)
    if invoice.due_at is None:
        _issue(invoice, due_days=due_days)
    return invoice


@transaction.atomic
def cancel_invoice(invoice: Invoice, *, reason: str = "", user=None) -> Invoice:
    """Annule une facture : réservée aux factures sans aucun règlement."""
    if (invoice.paid_amount or Decimal("0")) > 0:
        raise ValidationError(
            _(
                "Une facture avec règlement ne peut pas être annulée "
                "(remboursement hors périmètre)."
            )
        )
    invoice.transition_to(Invoice.Status.CANCELLED, reason=reason, user=user)
    return invoice


@transaction.atomic
def add_payment(
    invoice: Invoice,
    *,
    amount,
    method: str,
    paid_at=None,
    reference: str = "",
    user=None,
) -> Payment:
    """Enregistre un règlement et met à jour l'état de la facture.

    Un règlement supérieur au reste à payer est refusé ; le solde atteint,
    la facture passe « payée » (transition définitive).
    """
    if invoice.is_cancelled:
        raise ValidationError(_("Une facture annulée ne peut pas être réglée."))
    if invoice.status == Invoice.Status.DRAFT:
        raise ValidationError(_("Émettez la facture avant d'encaisser un règlement."))

    amount = Decimal(str(amount)).quantize(Decimal("0.01"))
    if amount <= 0:
        raise ValidationError({"amount": _("Le montant doit être strictement positif.")})

    # Verrou pessimiste : deux encaissements simultanés ne franchissent
    # jamais le total de la facture.
    locked = Invoice.all_objects.select_for_update().get(pk=invoice.pk)
    remaining = locked.total - (locked.paid_amount or Decimal("0"))
    if amount > remaining:
        raise ValidationError(
            {
                "amount": _("Le règlement dépasse le reste à payer (%(reste)s).")
                % {"reste": f"{remaining:.2f}"}
            }
        )

    payment = Payment(
        clinic=invoice.clinic,
        invoice=invoice,
        amount=amount,
        method=method,
        paid_at=paid_at or timezone.now(),
        reference=(reference or "").strip()[:60],
        created_by=user if getattr(user, "is_authenticated", False) else None,
    )
    payment.full_clean()
    payment.save()

    invoice.paid_amount = (invoice.paid_amount or Decimal("0")) + amount
    invoice.save(update_fields=["paid_amount", "updated_at"])
    if invoice.amount_due == 0:
        invoice.transition_to(Invoice.Status.PAID, user=user)
    elif invoice.can_transition_to(Invoice.Status.PARTIAL):
        invoice.transition_to(Invoice.Status.PARTIAL, user=user)
    return payment


def delete_payment(payment: Payment, *, user=None) -> None:
    """Retire un règlement (erreur de saisie) et recalcule la facture."""
    invoice = payment.invoice
    if invoice.status == Invoice.Status.PAID:
        invoice.paid_at = None
    invoice.paid_amount = max(
        (invoice.paid_amount or Decimal("0")) - payment.amount, Decimal("0")
    )
    invoice.save(update_fields=["paid_amount", "paid_at", "updated_at"])
    if invoice.status == Invoice.Status.PAID and invoice.amount_due > 0:
        invoice.status = Invoice.Status.PARTIAL
        invoice.save(update_fields=["status", "updated_at"])
    payment.delete()


def unpaid_invoices(clinic=None, *, as_of=None):
    """Factures émises ou partiellement payées, échues ou non.

    Voir :func:`overdue_invoices` pour les seuls impayés échus (relances).
    """
    as_of = as_of or timezone.localdate()
    queryset = Invoice.objects.filter(
        status__in=(Invoice.Status.ISSUED, Invoice.Status.PARTIAL)
    )
    if clinic is not None:
        queryset = queryset.filter(clinic=clinic)
    return queryset.select_related("patient", "clinic").order_by("due_at")


def overdue_invoices(clinic=None, *, as_of=None):
    """Impayés échus : base des relances et du rapport quotidien."""
    as_of = as_of or timezone.localdate()
    return unpaid_invoices(clinic, as_of=as_of).filter(due_at__lt=as_of)
