"""Sérialiseurs DRF des rendez-vous."""

from __future__ import annotations

from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from apps.accounts.models import Membership
from apps.patients.models import Patient

from .models import Appointment


class AppointmentSerializer(serializers.ModelSerializer):
    """Rendez-vous complet, avec le nom du patient et du praticien."""

    patient_name = serializers.CharField(source="patient.display_name", read_only=True)
    patient_reference = serializers.CharField(source="patient.reference", read_only=True)
    practitioner_name = serializers.CharField(
        source="practitioner.user.full_name", read_only=True
    )
    display_time = serializers.CharField(read_only=True)
    duration_minutes = serializers.IntegerField(read_only=True)

    class Meta:
        model = Appointment
        fields = [
            "id",
            "reference",
            "patient",
            "patient_reference",
            "patient_name",
            "practitioner",
            "practitioner_name",
            "start_at",
            "end_at",
            "display_time",
            "duration_minutes",
            "status",
            "kind",
            "reason",
            "notes",
            "cancelled_at",
            "cancellation_reason",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "reference",
            "end_at",
            "cancelled_at",
            "cancellation_reason",
            "created_at",
            "updated_at",
        ]

    def validate(self, attrs):
        attrs = super().validate(attrs)
        start_at = attrs.get("start_at") or getattr(self.instance, "start_at", None)
        end_at = attrs.get("end_at") or getattr(self.instance, "end_at", None)
        if start_at and end_at and end_at <= start_at:
            raise serializers.ValidationError(
                {"end_at": _("La fin doit être postérieure au début.")}
            )
        if start_at and self.instance is None and start_at < timezone.now():
            raise serializers.ValidationError(
                {"start_at": _("Impossible de prendre un rendez-vous dans le passé.")}
            )
        if self.instance is None:
            # À la création, seuls « à confirmer » et « confirmé » font sens.
            allowed = (Appointment.Status.PENDING, Appointment.Status.CONFIRMED)
            if attrs.get("status", Appointment.Status.PENDING) not in allowed:
                raise serializers.ValidationError(
                    {
                        "status": _(
                            "Un rendez-vous se crée « à confirmer » ou « confirmé »."
                        )
                    }
                )
        return attrs


class AppointmentStatusSerializer(serializers.Serializer):
    """Changement de statut (file d'attente, fin de consultation)."""

    status = serializers.ChoiceField(choices=Appointment.Status.choices)
    reason = serializers.CharField(
        required=False, allow_blank=True, max_length=255, default=""
    )


class SlotSerializer(serializers.Serializer):
    """Créneau disponible proposé à l'utilisateur."""

    start = serializers.DateTimeField()
    end = serializers.DateTimeField()
    label = serializers.SerializerMethodField()

    def get_label(self, obj) -> str:
        start, end = obj["start"], obj["end"]
        return f"{timezone.localtime(start):%H:%M} – {timezone.localtime(end):%H:%M}"


class AppointmentPatientAutocompleteSerializer(serializers.ModelSerializer):
    """Recherche patient pour l'accueil (prise de rendez-vous)."""

    full_name = serializers.CharField(source="display_name", read_only=True)
    display_phone = serializers.CharField(read_only=True)

    class Meta:
        model = Patient
        fields = ["id", "reference", "full_name", "display_phone"]


class PractitionerChoiceSerializer(serializers.ModelSerializer):
    """Praticien réservable, pour alimenter le sélecteur du mobile."""

    name = serializers.CharField(source="user.full_name", read_only=True)
    specialty = serializers.SerializerMethodField()
    color = serializers.SerializerMethodField()

    class Meta:
        model = Membership
        fields = ["id", "name", "specialty", "color"]

    def get_specialty(self, obj) -> str:
        profile = getattr(obj, "practitioner_profile", None)
        return profile.specialty.name if profile and profile.specialty_id else ""

    def get_color(self, obj) -> str:
        profile = getattr(obj, "practitioner_profile", None)
        return profile.calendar_color if profile else "#0d6efd"
