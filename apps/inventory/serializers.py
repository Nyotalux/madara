"""Sérialiseurs DRF du stock (jalon 6)."""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from .models import Product, StockMovement


class ProductSerializer(serializers.ModelSerializer):
    is_low_stock = serializers.BooleanField(read_only=True)
    stock_value = serializers.DecimalField(
        max_digits=12, decimal_places=2, read_only=True
    )

    class Meta:
        model = Product
        fields = [
            "id",
            "reference",
            "name",
            "display_name",
            "category",
            "barcode",
            "unit",
            "purchase_price",
            "sale_price",
            "stock_quantity",
            "low_stock_threshold",
            "is_low_stock",
            "stock_value",
            "notes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "reference",
            "stock_quantity",
            "is_low_stock",
            "stock_value",
            "created_at",
            "updated_at",
        ]

    def validate_category(self, value):
        if value not in Product.Category.values:
            raise serializers.ValidationError(_("Catégorie inconnue."))
        return value


class ProductCreateSerializer(serializers.ModelSerializer):
    """Création : la quantité initiale crée l'entrée d'origine du stock."""

    initial_quantity = serializers.DecimalField(
        max_digits=10,
        decimal_places=2,
        required=False,
        allow_null=True,
        min_value=0,
        write_only=True,
    )

    class Meta:
        model = Product
        fields = [
            "name",
            "category",
            "barcode",
            "unit",
            "purchase_price",
            "sale_price",
            "low_stock_threshold",
            "notes",
            "initial_quantity",
        ]

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if not (attrs.get("name") or "").strip():
            raise serializers.ValidationError(
                {"name": _("La désignation est obligatoire.")}
            )
        return attrs


class StockMovementSerializer(serializers.ModelSerializer):
    kind_label = serializers.CharField(source="get_kind_display", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    product_reference = serializers.CharField(source="product.reference", read_only=True)
    quantity_display = serializers.SerializerMethodField()

    class Meta:
        model = StockMovement
        fields = [
            "id",
            "product",
            "product_name",
            "product_reference",
            "kind",
            "kind_label",
            "quantity",
            "quantity_display",
            "stock_before",
            "stock_after",
            "reason",
            "moved_at",
            "created_at",
        ]
        read_only_fields = ["id", "stock_before", "stock_after", "created_at"]

    def get_quantity_display(self, obj) -> str:
        sign = {"IN": "+", "OUT": "−", "ADJUST": "="}.get(obj.kind, "")
        return f"{sign}{obj.quantity:n}"


class MovementCreateSerializer(serializers.Serializer):
    """Enregistrement d'un mouvement via l'API (entrée, sortie, ajustement)."""

    product = serializers.PrimaryKeyRelatedField(queryset=Product.objects.all())
    kind = serializers.ChoiceField(choices=StockMovement.Kind.choices)
    quantity = serializers.DecimalField(max_digits=10, decimal_places=2)
    reason = serializers.CharField(required=False, allow_blank=True, max_length=255)
    moved_at = serializers.DateTimeField(required=False)
