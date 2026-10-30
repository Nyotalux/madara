"""Tableau de bord et rapports de direction (jalon 7)."""

from __future__ import annotations

from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db.models import Count, F, Sum
from django.shortcuts import render
from django.utils import timezone

from apps.accounts.models import Membership
from apps.appointments.models import Appointment
from apps.appointments.services import clinic_timezone, day_bounds
from apps.billing.models import Invoice, Payment
from apps.billing.services import overdue_invoices
from apps.common.decorators import require_clinic
from apps.consultations.models import Consultation
from apps.inventory.models import Product
from apps.inventory.services import low_stock_products
from apps.patients.models import Patient
from apps.payroll import services as payroll_services
from apps.payroll.models import Payslip


@login_required
@require_clinic
def dashboard(request):
    """Page d'accueil du personnel : clinique, équipe, indicateurs (jalon 7)."""
    clinic = request.clinic

    staff_stats = (
        Membership.objects.filter(clinic=clinic)
        .values("role")
        .annotate(total=Count("id"))
    )
    role_counts = {row["role"]: row["total"] for row in staff_stats}

    tz = clinic_timezone(clinic)
    today = timezone.localdate()
    day_start, day_end = day_bounds(today, tz)
    today_appointments = (
        Appointment.objects.filter(
            clinic=clinic, start_at__lt=day_end, end_at__gt=day_start
        )
        .select_related("patient", "practitioner__user")
        .order_by("start_at")
    )
    membership = getattr(request, "clinic_membership", None)
    if (
        membership is not None
        and not request.user.is_platform_staff
        and membership.role in ("DOCTOR", "NURSE")
    ):
        today_appointments = today_appointments.filter(practitioner=membership)

    active_today = today_appointments.exclude(
        status__in=(Appointment.Status.DONE, Appointment.Status.CANCELLED)
    )
    next_appointment = (
        active_today.filter(start_at__gte=timezone.now()).first() or active_today.first()
    )
    waiting_count = active_today.filter(status=Appointment.Status.ARRIVED).count()

    # Jalon 7 Indicators
    patients_count = Patient.objects.filter(clinic=clinic, is_active=True).count()
    consultations_count = Consultation.objects.filter(clinic=clinic).count()

    products_qs = Product.objects.filter(clinic=clinic, is_active=True)
    products_count = products_qs.count()
    low_stock_count = low_stock_products(clinic).count()
    stock_value = products_qs.aggregate(
        val=Sum(F("stock_quantity") * F("purchase_price"))
    )["val"] or Decimal("0")

    invoices_qs = Invoice.objects.filter(clinic=clinic).prefetch_related("lines")
    total_invoiced = sum(
        inv.total for inv in invoices_qs.exclude(status=Invoice.Status.CANCELLED)
    )
    unpaid_overdue = list(overdue_invoices(clinic))
    overdue_amount = sum(inv.amount_due for inv in unpaid_overdue)

    context = {
        "clinic": clinic,
        "today_appointments": today_appointments,
        "today_count": today_appointments.count(),
        "waiting_count": waiting_count,
        "next_appointment": next_appointment,
        "role_counts": role_counts,
        "role_labels": dict(Membership.Role.choices),
        "total_staff": sum(role_counts.values()),
        "doctors": clinic.memberships.filter(
            role=Membership.Role.DOCTOR, is_active=True
        ).select_related("user"),
        "today": today,
        # Jalon 7 indicators
        "patients_count": patients_count,
        "consultations_count": consultations_count,
        "products_count": products_count,
        "low_stock_count": low_stock_count,
        "stock_value": stock_value,
        "total_invoiced": total_invoiced,
        "overdue_count": len(unpaid_overdue),
        "overdue_amount": overdue_amount,
    }
    return render(request, "dashboard/dashboard.html", context)


@login_required
@require_clinic
def reporting(request):
    """Rapports et indicateurs consolidés de la clinique (jalon 7)."""
    clinic = request.clinic

    patients_qs = Patient.all_objects.filter(clinic=clinic)
    consultations_qs = Consultation.objects.filter(clinic=clinic)
    products_qs = Product.objects.filter(clinic=clinic)
    invoices_qs = Invoice.objects.filter(clinic=clinic).prefetch_related("lines")
    payments_qs = Payment.objects.filter(clinic=clinic)

    current_period = payroll_services.current_period()
    payslips_qs = Payslip.objects.filter(clinic=clinic, period=current_period)

    context = {
        "clinic": clinic,
        "patients_stats": {
            "total": patients_qs.count(),
            "active": patients_qs.filter(is_active=True).count(),
            "insured": patients_qs.filter(is_insured=True, is_active=True).count(),
            "archived": patients_qs.filter(is_active=False).count(),
        },
        "consultations_stats": {
            "total": consultations_qs.count(),
            "completed": consultations_qs.filter(
                status=Consultation.Status.COMPLETED
            ).count(),
            "cancelled": consultations_qs.filter(
                status=Consultation.Status.CANCELLED
            ).count(),
        },
        "inventory_stats": {
            "products": products_qs.filter(is_active=True).count(),
            "low_stock": low_stock_products(clinic).count(),
            "stock_value": products_qs.filter(is_active=True).aggregate(
                val=Sum(F("stock_quantity") * F("purchase_price"))
            )["val"]
            or Decimal("0"),
        },
        "billing_stats": {
            "total_invoiced": sum(
                inv.total for inv in invoices_qs.exclude(status=Invoice.Status.CANCELLED)
            ),
            "total_collected": payments_qs.aggregate(total=Sum("amount"))["total"]
            or Decimal("0"),
            "overdue_count": len(list(overdue_invoices(clinic))),
        },
        "payroll_stats": {
            "period": current_period,
            "payslips_count": payslips_qs.count(),
            "total_net": sum(slip.net_amount for slip in payslips_qs),
        },
    }
    return render(request, "dashboard/reporting.html", context)
