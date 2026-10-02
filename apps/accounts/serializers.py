"""Sérialiseurs DRF."""

from __future__ import annotations

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from .models import (
    BlockedSlot,
    Clinic,
    Membership,
    OpeningHour,
    PractitionerProfile,
    Specialty,
    User,
)


class ClinicSerializer(serializers.ModelSerializer):
    staff_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Clinic
        fields = [
            "id",
            "name",
            "slug",
            "legal_name",
            "address",
            "city",
            "country",
            "phone",
            "email",
            "website",
            "tax_identifier",
            "license_number",
            "logo",
            "currency",
            "timezone",
            "language",
            "tax_rate",
            "appointment_slot_minutes",
            "dunning_days",
            "is_active",
            "staff_count",
            "created_at",
        ]
        read_only_fields = ["slug", "staff_count", "created_at"]


class PractitionerProfileSerializer(serializers.ModelSerializer):
    display_name = serializers.CharField(read_only=True)

    class Meta:
        model = PractitionerProfile
        fields = [
            "id",
            "profession",
            "specialty",
            "license_number",
            "diploma",
            "years_of_experience",
            "bio",
            "consultation_duration_minutes",
            "calendar_color",
            "display_name",
        ]


class MembershipSerializer(serializers.ModelSerializer):
    user_id = serializers.IntegerField(source="user.id", read_only=True)
    email = serializers.EmailField(source="user.email", read_only=True)
    full_name = serializers.CharField(source="user.full_name", read_only=True)
    phone = serializers.CharField(source="user.phone", read_only=True)
    role_display = serializers.CharField(source="get_role_display", read_only=True)
    clinic_name = serializers.CharField(source="clinic.name", read_only=True)
    practitioner_profile = PractitionerProfileSerializer(read_only=True)

    class Meta:
        model = Membership
        fields = [
            "id",
            "uuid",
            "user_id",
            "email",
            "full_name",
            "phone",
            "role",
            "role_display",
            "clinic",
            "clinic_name",
            "employee_number",
            "hire_date",
            "contract_end_date",
            "base_salary",
            "is_active",
            "notes",
            "practitioner_profile",
            "created_at",
        ]
        read_only_fields = ["uuid", "base_salary", "created_at"]


class UserSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(read_only=True)
    memberships = MembershipSerializer(many=True, read_only=True)
    role = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "email",
            "first_name",
            "last_name",
            "full_name",
            "phone",
            "locale",
            "avatar",
            "is_platform_staff",
            "is_active",
            "is_staff",
            "memberships",
            "role",
            "date_joined",
            "last_login",
        ]
        read_only_fields = [
            "is_platform_staff",
            "is_staff",
            "date_joined",
            "last_login",
        ]

    def get_role(self, obj) -> str | None:
        membership = obj.active_membership or obj.primary_membership
        return membership.role if membership else None


class MeSerializer(UserSerializer):
    """Profil de l'utilisateur connecté, enrichi du contexte clinique."""

    current_clinic = serializers.SerializerMethodField()
    clinics = serializers.SerializerMethodField()

    @extend_schema_field(ClinicSerializer(allow_null=True))
    def get_current_clinic(self, obj):
        clinic = self.context.get("clinic")
        return ClinicSerializer(clinic).data if clinic else None

    @extend_schema_field(ClinicSerializer(many=True))
    def get_clinics(self, obj):
        return ClinicSerializer(obj.clinics, many=True).data

    class Meta(UserSerializer.Meta):
        fields = (*UserSerializer.Meta.fields, "current_clinic", "clinics")


class SpecialtySerializer(serializers.ModelSerializer):
    class Meta:
        model = Specialty
        fields = ["id", "name", "code"]


class OpeningHourSerializer(serializers.ModelSerializer):
    weekday_display = serializers.CharField(source="get_weekday_display", read_only=True)

    class Meta:
        model = OpeningHour
        fields = [
            "id",
            "weekday",
            "weekday_display",
            "start_time",
            "end_time",
            "is_closed",
        ]


class BlockedSlotSerializer(serializers.ModelSerializer):
    practitioner_name = serializers.CharField(
        source="membership.user.full_name", read_only=True
    )

    class Meta:
        model = BlockedSlot
        fields = [
            "id",
            "membership",
            "practitioner_name",
            "start_at",
            "end_at",
            "reason",
        ]
