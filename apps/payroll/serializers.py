"""Sérialiseurs DRF de la paie (jalon 6)."""

from __future__ import annotations

import re
from decimal import Decimal

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from .models import Bonus, Payslip

_PERIOD_RE = re.compile(r"\d{4}-\d{2}")


def _validate_period(value: str) -> str:
    if not _PERIOD_RE.fullmatch(value or ""):
        raise serializers.ValidationError(_("La période suit le format AAAA-MM."))
    return value


class BonusSerializer(serializers.ModelSerializer):
    kind_label = serializers.CharField(source="get_kind_display", read_only=True)
    staff_name = serializers.CharField(source="membership.user.full_name", read_only=True)
    staff_reference = serializers.CharField(
        source="membership.employee_number", read_only=True
    )
    signed_amount = serializers.DecimalField(
        max_digits=10, decimal_places=2, read_only=True
    )

    class Meta:
        model = Bonus
        fields = [
            "id",
            "membership",
            "staff_name",
            "staff_reference",
            "period",
            "kind",
            "kind_label",
            "label",
            "amount",
            "signed_amount",
            "created_at",
        ]
        read_only_fields = ["id", "created_at"]

    def validate_period(self, value):
        return _validate_period(value)

    def validate_amount(self, value):
        if value is None or value <= 0:
            raise serializers.ValidationError(
                _("Le montant doit être strictement positif.")
            )
        return Decimal(str(value)).quantize(Decimal("0.01"))


class BonusCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Bonus
        fields = ["membership", "period", "kind", "label", "amount"]

    def validate_period(self, value):
        return _validate_period(value)

    def validate_amount(self, value):
        if value is None or value <= 0:
            raise serializers.ValidationError(
                _("Le montant doit être strictement positif.")
            )
        return Decimal(str(value)).quantize(Decimal("0.01"))


class PayslipSerializer(serializers.ModelSerializer):
    staff_name = serializers.CharField(source="membership.user.full_name", read_only=True)
    staff_reference = serializers.CharField(
        source="membership.employee_number", read_only=True
    )
    staff_email = serializers.CharField(source="membership.user.email", read_only=True)
    role = serializers.CharField(source="membership.role", read_only=True)
    net_amount = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)

    class Meta:
        model = Payslip
        fields = [
            "id",
            "reference",
            "membership",
            "staff_name",
            "staff_reference",
            "staff_email",
            "role",
            "period",
            "status",
            "base_amount",
            "bonus_amount",
            "deduction_amount",
            "net_amount",
            "paid_at",
            "cancelled_at",
            "cancellation_reason",
            "notes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "reference",
            "status",
            "paid_at",
            "cancelled_at",
            "cancellation_reason",
            "created_at",
            "updated_at",
        ]

    def validate_period(self, value):
        return _validate_period(value)


class PayslipUpdateSerializer(serializers.ModelSerializer):
    """Ajustement d'un bulletin brouillon (montants de la période)."""

    class Meta:
        model = Payslip
        fields = ["base_amount", "bonus_amount", "deduction_amount", "notes"]

    def validate(self, attrs):
        attrs = super().validate(attrs)
        for field in ("base_amount", "bonus_amount", "deduction_amount"):
            if field in attrs and attrs[field] is not None and attrs[field] < 0:
                raise serializers.ValidationError(
                    {field: _("Le montant ne peut pas être négatif.")}
                )
        if self.instance and self.instance.status != Payslip.Status.DRAFT:
            raise serializers.ValidationError(
                _("Seul un bulletin brouillon est modifiable.")
            )
        return attrs
