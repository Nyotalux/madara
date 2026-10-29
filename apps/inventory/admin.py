"""Administration Django du stock (jalon 6)."""

from __future__ import annotations

from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import Product, StockMovement


class StockMovementInline(admin.TabularInline):
    model = StockMovement
    extra = 0
    readonly_fields = ("created_at", "created_by")
    verbose_name = _("mouvement")
    verbose_name_plural = _("mouvements")


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = (
        "reference",
        "name",
        "category",
        "unit",
        "stock_quantity",
        "low_stock_threshold",
        "purchase_price",
        "sale_price",
        "clinic",
        "is_active",
    )
    list_filter = ("category", "clinic", "is_active")
    search_fields = ("reference", "name", "barcode")
    readonly_fields = (
        "reference",
        "stock_quantity",
        "created_at",
        "updated_at",
        "created_by",
    )
    inlines = [StockMovementInline]
    list_select_related = ("clinic",)


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = (
        "product",
        "kind",
        "quantity",
        "stock_before",
        "stock_after",
        "reason",
        "moved_at",
        "clinic",
    )
    list_filter = ("kind", "clinic")
    search_fields = ("product__name", "product__reference", "reason")
    raw_id_fields = ("product",)
    date_hierarchy = "moved_at"
    readonly_fields = (
        "stock_before",
        "stock_after",
        "created_at",
        "updated_at",
        "created_by",
    )
