"""Administration Django de la paie (jalon 6)."""

from __future__ import annotations

from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import Bonus, Payslip


@admin.register(Bonus)
class BonusAdmin(admin.ModelAdmin):
    list_display = (
        "membership",
        "period",
        "kind",
        "label",
        "amount",
        "clinic",
    )
    list_filter = ("kind", "clinic", "period")
    search_fields = (
        "label",
        "membership__user__first_name",
        "membership__user__last_name",
    )
    raw_id_fields = ("membership",)
    list_select_related = ("clinic", "membership__user")


@admin.register(Payslip)
class PayslipAdmin(admin.ModelAdmin):
    list_display = (
        "reference",
        "membership",
        "period",
        "status_badge",
        "base_amount",
        "bonus_amount",
        "deduction_amount",
        "paid_at",
        "clinic",
    )
    list_filter = ("status", "clinic", "period")
    search_fields = (
        "reference",
        "membership__user__first_name",
        "membership__user__last_name",
        "membership__user__email",
    )
    raw_id_fields = ("membership",)
    readonly_fields = (
        "reference",
        "paid_at",
        "cancelled_at",
        "cancellation_reason",
        "created_at",
        "updated_at",
        "created_by",
        "is_active",
        "deleted_at",
        "deletion_reason",
    )
    date_hierarchy = "created_at"
    list_select_related = ("clinic", "membership")

    @admin.display(description=_("état"), ordering="status")
    def status_badge(self, obj):
        return obj.get_status_display()
