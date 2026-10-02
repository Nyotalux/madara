"""Sérialiseurs DRF des dossiers patients."""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from .models import Patient


class PatientListSerializer(serializers.ModelSerializer):
    """Version compacte pour les listes et la recherche mobile."""

    full_name = serializers.CharField(source="display_name", read_only=True)
    initials = serializers.CharField(read_only=True)
    age = serializers.IntegerField(read_only=True)
    display_phone = serializers.CharField(read_only=True)

    class Meta:
        model = Patient
        fields = [
            "id",
            "uuid",
            "reference",
            "first_name",
            "last_name",
            "full_name",
            "initials",
            "age",
            "gender",
            "birth_date",
            "display_phone",
            "city",
            "is_insured",
            "is_active",
        ]
        read_only_fields = ["id", "uuid", "reference", "created_at", "updated_at"]


class PatientSerializer(serializers.ModelSerializer):
    """Fiche complète : écriture réservée au personnel soignant."""

    full_name = serializers.CharField(source="display_name", read_only=True)
    display_phone = serializers.CharField(read_only=True)
    insurance_is_expired = serializers.BooleanField(read_only=True)
    has_medical_flags = serializers.BooleanField(read_only=True)

    class Meta:
        model = Patient
        fields = [
            "id",
            "uuid",
            "reference",
            "first_name",
            "last_name",
            "full_name",
            "birth_date",
            "gender",
            "cin",
            "passport",
            "social_security",
            "phone",
            "phone_secondary",
            "display_phone",
            "email",
            "address",
            "city",
            "emergency_contact_name",
            "emergency_contact_phone",
            "is_insured",
            "insurer",
            "insurance_number",
            "insurance_expiry",
            "insurance_is_expired",
            "blood_type",
            "height_cm",
            "weight_kg",
            "allergies",
            "chronic_conditions",
            "current_medication",
            "is_pregnant",
            "preferred_language",
            "notes",
            "has_medical_flags",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "uuid",
            "reference",
            "is_active",
            "created_at",
            "updated_at",
        ]

    def validate_birth_date(self, value):
        from django.utils import timezone

        if value and value > timezone.localdate():
            raise serializers.ValidationError(_("La date de naissance est future."))
        return value

    def validate(self, attrs):
        instance = self.instance
        is_insured = attrs.get("is_insured", getattr(instance, "is_insured", False))
        insurer = (attrs.get("insurer", getattr(instance, "insurer", "")) or "").strip()
        if is_insured and not insurer:
            raise serializers.ValidationError(
                {"insurer": _("Indiquez l'assureur si le patient est assuré.")}
            )
        gender = attrs.get("gender", getattr(instance, "gender", ""))
        is_pregnant = attrs.get("is_pregnant", getattr(instance, "is_pregnant", False))
        if is_pregnant and gender == Patient.Gender.MALE:
            raise serializers.ValidationError(
                {"is_pregnant": _("Incohérent avec le sexe indiqué.")}
            )
        return attrs


class PatientArchiveSerializer(serializers.Serializer):
    """Archivage logique avec motif."""

    reason = serializers.CharField(
        required=False, allow_blank=True, max_length=255, default=""
    )
