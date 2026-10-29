"""Vues web de la paie (jalon 6).

Périodes de paie, bulletins (génération, ajustement, paiement, annulation),
primes et retenues. La paie est réservée à l'administrateur de la clinique
et à la comptabilité (cf. ``apps.payroll.services.PAYROLL_ROLES``).
"""

from __future__ import annotations

from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _

from apps.common.decorators import require_clinic, require_roles
from apps.common.pagination import paginate

from . import services
from .forms import BonusForm, PayrollFilterForm, PayslipForm
from .models import Bonus, Payslip

PAYROLL_ROLES = services.PAYROLL_ROLES


# ---------------------------------------------------------------------------
# Lecture
# ---------------------------------------------------------------------------


@login_required
@require_clinic
@require_roles(*PAYROLL_ROLES)
def payroll_home(request):
    """Accueil paie : périodes, compteurs, bulletins récents."""
    clinic = request.clinic
    current = services.current_period()
    summary = services.period_summary(clinic, current)

    periods = list(services.latest_periods(clinic))
    if current not in periods:
        periods.insert(0, current)
    period_cards = [services.period_summary(clinic, period) for period in periods[:6]]

    payslips = Payslip.objects.filter(clinic=clinic).select_related(
        "membership", "membership__user"
    )

    form = PayrollFilterForm(request.GET or None)
    period = (request.GET.get("period") or "").strip()
    if period:
        payslips = payslips.filter(period=period)
    status = request.GET.get("status") or ""
    if status in Payslip.Status.values:
        payslips = payslips.filter(status=status)
    needle = (request.GET.get("q") or "").strip()
    if len(needle) >= 2:
        payslips = payslips.filter(
            Q(membership__user__first_name__icontains=needle)
            | Q(membership__user__last_name__icontains=needle)
            | Q(membership__user__email__icontains=needle)
            | Q(reference__icontains=needle)
        )

    page = paginate(request, payslips)
    context = {
        "form": form,
        "payslips": page["page_obj"].object_list,
        "result_count": payslips.count(),
        "summary": summary,
        "period_cards": period_cards,
        "can_write": _has_role(request, PAYROLL_ROLES),
        **page,
    }
    return render(request, "payroll/payroll_home.html", context)


@login_required
@require_clinic
@require_roles(*PAYROLL_ROLES)
def payslip_detail(request, pk: int):
    """Fiche bulletin : montants, primes de la période, actions."""
    payslip = _payslip_or_404(request, pk)
    bonus_list = Bonus.objects.filter(
        clinic=request.clinic,
        membership=payslip.membership,
        period=payslip.period,
    ).select_related("membership__user")
    context = {
        "payslip": payslip,
        "bonus_list": bonus_list,
        "can_write": _has_role(request, PAYROLL_ROLES),
    }
    return render(request, "payroll/payslip_detail.html", context)


@login_required
@require_clinic
@require_roles(*PAYROLL_ROLES)
def period_detail(request, period: str):
    """Période de paie : récapitulatif, bulletins, génération."""
    clinic = request.clinic
    try:
        period = services.normalize_period(period)
    except ValidationError as error:
        messages.error(request, services_error_text(error))
        return redirect("web:payroll-home")

    summary = services.period_summary(clinic, period)
    payslips = (
        Payslip.objects.filter(clinic=clinic, period=period)
        .select_related("membership", "membership__user")
        .order_by("membership__user__last_name")
    )
    bonus_list = Bonus.objects.filter(clinic=clinic, period=period).select_related(
        "membership__user"
    )
    context = {
        "period": period,
        "summary": summary,
        "payslips": payslips,
        "bonus_list": bonus_list,
        "can_write": _has_role(request, PAYROLL_ROLES),
        "today": date.today(),
    }
    return render(request, "payroll/period_detail.html", context)


# ---------------------------------------------------------------------------
# Écriture
# ---------------------------------------------------------------------------


@login_required
@require_clinic
@require_roles(*PAYROLL_ROLES)
def period_generate(request, period: str):
    """Établit les bulletins manquants de la période."""
    if request.method == "POST":
        try:
            created = services.generate_payslips(
                request.clinic, period=period, user=request.user
            )
        except ValidationError as error:
            messages.error(request, services_error_text(error))
        else:
            if created:
                messages.success(
                    request,
                    _("%(count)s bulletin(s) établi(s) pour %(period)s.")
                    % {"count": len(created), "period": period},
                )
            else:
                messages.info(
                    request,
                    _("Aucun bulletin à établir : la période est déjà couverte."),
                )
    return redirect("web:period-detail", period=period)


@login_required
@require_clinic
@require_roles(*PAYROLL_ROLES)
def payslip_edit(request, pk: int):
    """Ajuste un bulletin brouillon (montants, notes)."""
    payslip = _payslip_or_404(request, pk)
    if payslip.status != Payslip.Status.DRAFT:
        messages.error(
            request,
            _(
                "Seul un bulletin brouillon est modifiable : %(reference)s est déjà traité."
            )
            % {"reference": payslip.reference},
        )
        return redirect("web:payslip-detail", pk=payslip.pk)

    form = PayslipForm(request.POST or None, instance=payslip)
    if request.method == "POST":
        if form.is_valid():
            try:
                services.update_payslip(
                    payslip,
                    base_amount=form.cleaned_data.get("base_amount"),
                    bonus_amount=form.cleaned_data.get("bonus_amount"),
                    deduction_amount=form.cleaned_data.get("deduction_amount"),
                    notes=form.cleaned_data.get("notes") or "",
                    user=request.user,
                )
            except ValidationError as error:
                messages.error(request, services_error_text(error))
            else:
                messages.success(request, _("Bulletin mis à jour."))
                return redirect("web:payslip-detail", pk=payslip.pk)
        messages.error(request, _("Vérifiez le formulaire."))

    return render(
        request,
        "payroll/payslip_form.html",
        {"form": form, "payslip": payslip},
    )


@login_required
@require_clinic
@require_roles(*PAYROLL_ROLES)
def payslip_pay(request, pk: int):
    """Paiement définitif d'un bulletin."""
    payslip = _payslip_or_404(request, pk)
    if request.method == "POST":
        try:
            services.pay_payslip(payslip, user=request.user)
        except ValidationError as error:
            messages.error(request, services_error_text(error))
        else:
            messages.success(
                request,
                _("Bulletin %(reference)s payé.") % {"reference": payslip.reference},
            )
    return redirect("web:payslip-detail", pk=payslip.pk)


@login_required
@require_clinic
@require_roles(*PAYROLL_ROLES)
def payslip_cancel(request, pk: int):
    """Annule un bulletin impayé (motif obligatoire)."""
    payslip = _payslip_or_404(request, pk)
    if request.method == "POST":
        reason = (request.POST.get("reason") or "").strip()
        try:
            services.cancel_payslip(payslip, reason=reason, user=request.user)
        except ValidationError as error:
            messages.error(request, services_error_text(error))
        else:
            messages.success(
                request,
                _("Bulletin %(reference)s annulé.") % {"reference": payslip.reference},
            )
    return redirect("web:payslip-detail", pk=payslip.pk)


@login_required
@require_clinic
@require_roles(*PAYROLL_ROLES)
def bonus_create(request):
    """Rajoute une prime ou une retenue pour la période en cours ou choisie."""
    membership_id = request.GET.get("membership")
    period = (request.GET.get("period") or services.current_period()).strip()
    initial = {"period": period}
    if membership_id:
        initial["membership"] = membership_id
    form = BonusForm(request.POST or None, clinic=request.clinic, initial=initial)

    if request.method == "POST":
        if form.is_valid():
            try:
                bonus = services.add_bonus(
                    request.clinic,
                    form.cleaned_data["membership"],
                    kind=form.cleaned_data["kind"],
                    label=form.cleaned_data["label"],
                    amount=form.cleaned_data["amount"],
                    period=form.cleaned_data["period"],
                    user=request.user,
                )
            except ValidationError as error:
                messages.error(request, services_error_text(error))
            else:
                messages.success(
                    request,
                    _("%(libelle)s enregistré pour %(period)s.")
                    % {"libelle": bonus.label, "period": bonus.period},
                )
                return redirect("web:period-detail", period=bonus.period)
        messages.error(request, _("Vérifiez le formulaire."))

    return render(
        request,
        "payroll/bonus_form.html",
        {"form": form, "period": period},
    )


@login_required
@require_clinic
@require_roles(*PAYROLL_ROLES)
def bonus_remove(request, pk: int):
    """Retire une prime ou une retenue (erreur de saisie)."""
    bonus = get_object_or_404(
        Bonus.objects.filter(clinic=request.clinic).select_related("membership__user"),
        pk=pk,
    )
    if request.method == "POST":
        try:
            services.remove_bonus(bonus, user=request.user)
        except ValidationError as error:
            messages.error(request, services_error_text(error))
        else:
            messages.success(request, _("Ligne retirée."))
    return redirect("web:period-detail", period=bonus.period)


# ---------------------------------------------------------------------------
# Aides
# ---------------------------------------------------------------------------


def _payslip_or_404(request, pk: int) -> Payslip:
    return get_object_or_404(
        Payslip.objects.filter(clinic=request.clinic).select_related(
            "membership", "membership__user"
        ),
        pk=pk,
    )


def _has_role(request, roles) -> bool:
    if request.user.is_platform_staff:
        return True
    membership = getattr(request, "clinic_membership", None)
    return bool(membership and membership.role in roles)


def services_error_text(error: ValidationError) -> str:
    if hasattr(error, "message_dict"):
        parts = []
        for _field, messages_list in error.message_dict.items():
            parts.extend(str(message) for message in messages_list)
        return " ".join(parts)
    return " ".join(str(message) for message in error.messages)
