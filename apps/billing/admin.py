"""Administration Django de la facturation (jalon 6)."""

from __future__ import annotations

from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import Invoice, InvoiceLine, Payment


class InvoiceLineInline(admin.TabularInline):
    model = InvoiceLine
    extra = 0
    readonly_fields = ("line_total_display",)
    verbose_name = _("ligne de facture")
    verbose_name_plural = _("lignes de facture")

    @admin.display(description=_("total ligne"))
    def line_total_display(self, obj):
        return obj.line_total if obj.pk else "—"


class PaymentInline(admin.TabularInline):
    model = Payment
    extra = 0
    readonly_fields = ("created_at", "created_by")
    verbose_name = _("règlement")
    verbose_name_plural = _("règlements")


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = (
        "reference",
        "patient",
        "status_badge",
        "total_display",
        "paid_amount",
        "due_at",
        "clinic",
        "is_active",
    )
    list_filter = ("status", "clinic", "due_at", "is_active")
    search_fields = (
        "reference",
        "patient__first_name",
        "patient__last_name",
        "patient__phone",
    )
    readonly_fields = (
        "reference",
        "paid_amount",
        "paid_at",
        "cancelled_at",
        "cancellation_reason",
        "created_at",
        "updated_at",
        "created_by",
    )
    date_hierarchy = "created_at"
    inlines = [InvoiceLineInline, PaymentInline]
    list_select_related = ("patient", "clinic")

    @admin.display(description=_("état"), ordering="status")
    def status_badge(self, obj):
        return obj.get_status_display()

    @admin.display(description=_("total"))
    def total_display(self, obj):
        return obj.total


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = (
        "invoice",
        "amount",
        "method",
        "paid_at",
        "reference",
        "clinic",
    )
    list_filter = ("method", "clinic", "paid_at")
    search_fields = ("reference", "invoice__reference", "invoice__patient__last_name")
    raw_id_fields = ("invoice",)
    date_hierarchy = "paid_at"
