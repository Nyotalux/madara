"""Vues web de la facturation (jalon 6).

Liste filtrable avec compteurs, création avec lignes dynamiques (formset),
émission, encaissement, impression et annulation. Les praticiens n'ont pas
accès à la facturation (cf. ``apps.billing.permissions`` et les décorateurs).
"""

from __future__ import annotations

from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.common.decorators import require_clinic, require_roles
from apps.common.pagination import paginate

from .forms import (
    InvoiceFilterForm,
    InvoiceForm,
    InvoiceLineFormSet,
    PaymentForm,
)
from .models import Invoice, Payment
from .services import (
    BILLING_CANCEL_ROLES,
    BILLING_READ_ROLES,
    BILLING_WRITE_ROLES,
    add_payment,
    cancel_invoice,
    create_invoice,
    delete_payment,
    issue_invoice,
    overdue_invoices,
    unpaid_invoices,
)

INVOICE_SCOPED_STATUSES = {
    "dues": (Invoice.Status.ISSUED, Invoice.Status.PARTIAL),
    "en-cours": (Invoice.Status.ISSUED, Invoice.Status.PARTIAL),
    "payees": (Invoice.Status.PAID,),
}


# ---------------------------------------------------------------------------
# Lecture
# ---------------------------------------------------------------------------


@login_required
@require_clinic
@require_roles(*BILLING_READ_ROLES)
def invoice_list(request):
    """Factures de la clinique : recherche, état, impayés, compteurs."""
    clinic = request.clinic
    invoices = Invoice.objects.filter(clinic=clinic).select_related(
        "patient", "consultation"
    )

    form = InvoiceFilterForm(request.GET or None)
    needle = (request.GET.get("q") or "").strip()
    if len(needle) >= 2:
        invoices = invoices.filter(
            Q(reference__icontains=needle)
            | Q(patient__first_name__icontains=needle)
            | Q(patient__last_name__icontains=needle)
            | Q(patient__phone__icontains=needle)
        )
    status = request.GET.get("status") or ""
    if status in Invoice.Status.values:
        invoices = invoices.filter(status=status)
    reglement = request.GET.get("impayees") or ""
    if reglement == "dues":
        invoices = overdue_invoices().filter(clinic=clinic)
    elif reglement == "en-cours":
        invoices = invoices.filter(
            status__in=(Invoice.Status.ISSUED, Invoice.Status.PARTIAL)
        )
    elif reglement == "payees":
        invoices = invoices.filter(status=Invoice.Status.PAID)

    page = paginate(request, invoices)
    today = timezone.localdate()
    month_start = today.replace(day=1)
    stats = {
        "issued": Invoice.objects.filter(
            clinic=clinic, status=Invoice.Status.ISSUED
        ).count(),
        "overdue": overdue_invoices(clinic).count(),
        "collected_month": Payment.objects.filter(
            clinic=clinic, paid_at__gte=month_start
        ).aggregate(total=Sum("amount"))["total"]
        or Decimal("0"),
        "outstanding": sum(invoice.amount_due for invoice in unpaid_invoices(clinic)),
    }
    context = {
        "form": form,
        "invoices": page["page_obj"].object_list,
        "result_count": invoices.count(),
        "stats": stats,
        "today": today,
        **page,
    }
    return render(request, "billing/invoice_list.html", context)


@login_required
@require_clinic
@require_roles(*BILLING_READ_ROLES)
def invoice_detail(request, pk: int):
    """Fiche facture : lignes, règlements, actions disponibles."""
    invoice = _invoice_or_404(request, pk)
    context = {
        "invoice": invoice,
        "can_write": _has_role(request, BILLING_WRITE_ROLES),
        "can_cancel": _has_role(request, BILLING_CANCEL_ROLES),
    }
    return render(request, "billing/invoice_detail.html", context)


@login_required
@require_clinic
@require_roles(*BILLING_READ_ROLES)
def invoice_print(request, pk: int):
    """Version imprimable (lecture seule, sans actions)."""
    invoice = _invoice_or_404(request, pk)
    return render(request, "billing/invoice_print.html", {"invoice": invoice})


# ---------------------------------------------------------------------------
# Écriture
# ---------------------------------------------------------------------------


@login_required
@require_clinic
@require_roles(*BILLING_WRITE_ROLES)
def invoice_create(request):
    """Nouvelle facture : en-tête + cinq lignes de prestation (formset)."""
    clinic = request.clinic
    form = InvoiceForm(clinic=clinic)
    formset = InvoiceLineFormSet()

    if request.method == "POST":
        form = InvoiceForm(request.POST, clinic=clinic)
        formset = InvoiceLineFormSet(request.POST)
        if form.is_valid() and formset.is_valid():
            lines = [
                cleaned
                for subform in formset
                for cleaned in [subform.cleaned_line()]
                if cleaned
            ]
            try:
                invoice = create_invoice(
                    clinic=clinic,
                    patient=form.cleaned_data["patient"],
                    consultation=form.cleaned_data.get("consultation"),
                    tax_rate=form.cleaned_data.get("tax_rate"),
                    due_days=form.cleaned_data.get("due_days"),
                    status=(
                        Invoice.Status.ISSUED
                        if request.POST.get("emit") == "1"
                        else Invoice.Status.DRAFT
                    ),
                    notes=form.cleaned_data.get("notes") or "",
                    lines=lines,
                    user=request.user,
                )
            except ValidationError as error:
                messages.error(request, _format_error(error))
            else:
                messages.success(
                    request,
                    _("Facture %(reference)s créée.") % {"reference": invoice.reference},
                )
                return redirect("web:invoice-detail", pk=invoice.pk)
        messages.error(request, _("Vérifiez le formulaire de facture."))

    return render(
        request,
        "billing/invoice_form.html",
        {"form": form, "formset": formset},
    )


@login_required
@require_clinic
@require_roles(*BILLING_WRITE_ROLES)
def invoice_edit(request, pk: int):
    """Modification d'un brouillon : en-tête et lignes."""
    invoice = _invoice_or_404(request, pk)
    if invoice.status != Invoice.Status.DRAFT:
        messages.error(
            request,
            _("Seul un brouillon est modifiable ; la facture %(reference)s est émise.")
            % {"reference": invoice.reference},
        )
        return redirect("web:invoice-detail", pk=invoice.pk)

    form = InvoiceForm(request.POST or None, instance=invoice, clinic=request.clinic)
    if request.method == "POST":
        if form.is_valid():
            invoice = form.save(commit=False)
            invoice.tax_rate = form.cleaned_data.get("tax_rate") or invoice.tax_rate
            invoice.save()
            messages.success(request, _("Facture mise à jour."))
            return redirect("web:invoice-detail", pk=invoice.pk)
        messages.error(request, _("Vérifiez le formulaire."))

    return render(
        request,
        "billing/invoice_form.html",
        {"form": form, "invoice": invoice, "editing": True},
    )


@login_required
@require_clinic
@require_roles(*BILLING_WRITE_ROLES)
def invoice_issue(request, pk: int):
    """Émet un brouillon : échéance calculée, lignes verrouillées."""
    invoice = _invoice_or_404(request, pk)
    if request.method == "POST":
        try:
            issue_invoice(invoice, user=request.user)
        except ValidationError as error:
            messages.error(request, _format_error(error))
        else:
            messages.success(
                request,
                _("Facture %(reference)s émise.") % {"reference": invoice.reference},
            )
    return redirect("web:invoice-detail", pk=invoice.pk)


@login_required
@require_clinic
@require_roles(*BILLING_CANCEL_ROLES)
def invoice_cancel(request, pk: int):
    """Annule une facture sans règlement (motif obligatoire)."""
    invoice = _invoice_or_404(request, pk)
    if request.method == "POST":
        reason = (request.POST.get("reason") or "").strip()
        try:
            cancel_invoice(invoice, reason=reason, user=request.user)
        except ValidationError as error:
            messages.error(request, _format_error(error))
        else:
            messages.success(
                request,
                _("Facture %(reference)s annulée.") % {"reference": invoice.reference},
            )
    return redirect("web:invoice-detail", pk=invoice.pk)


@login_required
@require_clinic
@require_roles(*BILLING_WRITE_ROLES)
def invoice_payment(request, pk: int):
    """Encaisse un règlement (partiel ou solde)."""
    invoice = _invoice_or_404(request, pk)
    form = PaymentForm(request.POST or None, invoice=invoice)
    if request.method == "POST" and form.is_valid():
        try:
            payment = add_payment(
                invoice,
                amount=form.cleaned_data["amount"],
                method=form.cleaned_data["method"],
                paid_at=form.cleaned_data.get("paid_at"),
                reference=form.cleaned_data.get("reference") or "",
                user=request.user,
            )
        except ValidationError as error:
            messages.error(request, _format_error(error))
        else:
            messages.success(
                request,
                _("Règlement de %(montant)s enregistré.")
                % {"montant": f"{payment.amount:.2f}"},
            )
            return redirect("web:invoice-detail", pk=invoice.pk)
    return render(
        request,
        "billing/payment_form.html",
        {"form": form, "invoice": invoice},
    )


@login_required
@require_clinic
@require_roles(*BILLING_WRITE_ROLES)
def payment_delete(request, pk: int, payment_pk: int):
    """Retire un règlement (erreur de saisie) et recalcule la facture."""
    invoice = _invoice_or_404(request, pk)
    payment = get_object_or_404(Payment.objects.filter(invoice=invoice), pk=payment_pk)
    if request.method == "POST":
        delete_payment(payment, user=request.user)
        messages.success(request, _("Règlement retiré."))
    return redirect("web:invoice-detail", pk=invoice.pk)


# ---------------------------------------------------------------------------
# Aides
# ---------------------------------------------------------------------------


def _invoice_or_404(request, pk: int) -> Invoice:
    return get_object_or_404(
        Invoice.objects.filter(clinic=request.clinic).select_related("patient"),
        pk=pk,
    )


def _has_role(request, roles) -> bool:
    if request.user.is_platform_staff:
        return True
    membership = getattr(request, "clinic_membership", None)
    return bool(membership and membership.role in roles)


def _format_error(error: ValidationError) -> str:
    if hasattr(error, "message_dict"):
        parts = []
        for _field, messages_list in error.message_dict.items():
            parts.extend(str(message) for message in messages_list)
        return " ".join(parts)
    return " ".join(str(message) for message in error.messages)
