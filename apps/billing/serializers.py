"""Sérialiseurs DRF de la facturation (jalon 6)."""

from __future__ import annotations

from decimal import Decimal

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from .models import Invoice, Payment


class InvoiceLineSerializer(serializers.Serializer):
    """Ligne de prestation à la création d'une facture."""

    label = serializers.CharField(max_length=160)
    quantity = serializers.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=Decimal("1"),
        min_value=Decimal("0.01"),
    )
    unit_price = serializers.DecimalField(
        max_digits=10, decimal_places=2, min_value=Decimal("0")
    )


class PaymentSerializer(serializers.ModelSerializer):
    method_label = serializers.CharField(source="get_method_display", read_only=True)

    class Meta:
        model = Payment
        fields = [
            "id",
            "invoice",
            "amount",
            "method",
            "method_label",
            "paid_at",
            "reference",
            "created_at",
        ]
        read_only_fields = ["id", "created_at"]


class InvoiceSerializer(serializers.ModelSerializer):
    patient_name = serializers.CharField(source="patient.display_name", read_only=True)
    patient_reference = serializers.CharField(source="patient.reference", read_only=True)
    consultation_reference = serializers.CharField(
        source="consultation.reference", read_only=True
    )
    amount_excl_tax = serializers.DecimalField(
        max_digits=10, decimal_places=2, read_only=True
    )
    tax_amount = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    total = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    amount_due = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    is_overdue = serializers.BooleanField(read_only=True)

    class Meta:
        model = Invoice
        fields = [
            "id",
            "reference",
            "patient",
            "patient_name",
            "patient_reference",
            "consultation",
            "consultation_reference",
            "status",
            "issued_at",
            "due_at",
            "tax_rate",
            "amount_excl_tax",
            "tax_amount",
            "total",
            "paid_amount",
            "amount_due",
            "is_overdue",
            "notes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "reference",
            "status",
            "issued_at",
            "paid_amount",
            "created_at",
            "updated_at",
        ]


class InvoiceCreateSerializer(serializers.ModelSerializer):
    """Création : lignes imbriquées, émission optionnelle en un appel."""

    lines = InvoiceLineSerializer(many=True, write_only=True)
    emit = serializers.BooleanField(default=False, write_only=True)
    due_days = serializers.IntegerField(default=None, write_only=True, min_value=1)

    class Meta:
        model = Invoice
        fields = [
            "patient",
            "consultation",
            "tax_rate",
            "notes",
            "lines",
            "emit",
            "due_days",
        ]

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if not attrs.get("lines"):
            raise serializers.ValidationError(
                {"lines": _("Ajoutez au moins une prestation avec un prix.")}
            )
        return attrs


class InvoicePaymentSerializer(serializers.Serializer):
    """Encaissement d'une facture via l'API."""

    amount = serializers.DecimalField(
        max_digits=10, decimal_places=2, min_value=Decimal("0.01")
    )
    method = serializers.ChoiceField(choices=Payment.Method.choices)
    paid_at = serializers.DateTimeField(required=False)
    reference = serializers.CharField(required=False, allow_blank=True, max_length=60)
