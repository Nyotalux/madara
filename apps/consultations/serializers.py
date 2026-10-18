"""Sérialiseurs DRF des consultations et ordonnances."""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from apps.accounts.models import Membership
from apps.appointments.models import Appointment
from apps.patients.models import Patient

from .models import Consultation, Prescription, PrescriptionItem, VitalSignsMixin


class VitalSignsSerializerMixin(serializers.Serializer):
    """Champs de constantes partagés (lecture/écriture)."""

    temperature_c = serializers.DecimalField(
        max_digits=4, decimal_places=1, required=False, allow_null=True
    )
    systolic = serializers.IntegerField(required=False, allow_null=True)
    diastolic = serializers.IntegerField(required=False, allow_null=True)
    heart_rate = serializers.IntegerField(required=False, allow_null=True)
    respiratory_rate = serializers.IntegerField(required=False, allow_null=True)
    oxygen_saturation = serializers.IntegerField(required=False, allow_null=True)
    blood_glucose = serializers.DecimalField(
        max_digits=5, decimal_places=2, required=False, allow_null=True
    )
    weight_kg = serializers.DecimalField(
        max_digits=5, decimal_places=1, required=False, allow_null=True
    )
    height_cm = serializers.IntegerField(required=False, allow_null=True)


class ConsultationListSerializer(serializers.ModelSerializer):
    """Version compacte pour les listes et l'agenda."""

    patient_name = serializers.CharField(source="patient.display_name", read_only=True)
    patient_reference = serializers.CharField(source="patient.reference", read_only=True)
    practitioner_name = serializers.CharField(
        source="practitioner.user.full_name", read_only=True
    )
    display_date = serializers.CharField(read_only=True)
    duration_minutes = serializers.IntegerField(read_only=True)
    prescription_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Consultation
        fields = [
            "id",
            "reference",
            "patient",
            "patient_reference",
            "patient_name",
            "practitioner",
            "practitioner_name",
            "status",
            "kind",
            "started_at",
            "ended_at",
            "display_date",
            "duration_minutes",
            "chief_complaint",
            "diagnosis",
            "prescription_count",
        ]
        read_only_fields = fields


class ConsultationSerializer(VitalSignsSerializerMixin, serializers.ModelSerializer):
    """Compte rendu complet d'une consultation."""

    patient_name = serializers.CharField(source="patient.display_name", read_only=True)
    practitioner_name = serializers.CharField(
        source="practitioner.user.full_name", read_only=True
    )
    blood_pressure_display = serializers.CharField(read_only=True)
    bmi = serializers.FloatField(read_only=True)
    duration_minutes = serializers.IntegerField(read_only=True)
    prescription_count = serializers.IntegerField(read_only=True)

    patient = serializers.PrimaryKeyRelatedField(
        queryset=Patient.objects.all(), required=False
    )
    practitioner = serializers.PrimaryKeyRelatedField(
        queryset=Membership.objects.all(), required=False
    )
    appointment = serializers.PrimaryKeyRelatedField(
        queryset=Appointment.objects.all(),
        required=False,
        allow_null=True,
    )

    class Meta:
        model = Consultation
        fields = [
            "id",
            "reference",
            "patient",
            "patient_name",
            "practitioner",
            "practitioner_name",
            "appointment",
            "status",
            "kind",
            "started_at",
            "ended_at",
            "chief_complaint",
            "history",
            "examination",
            "diagnosis",
            "treatment",
            "follow_up_date",
            *VitalSignsMixin.VITAL_FIELDS,
            "blood_pressure_display",
            "bmi",
            "duration_minutes",
            "prescription_count",
            "notes",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "reference",
            "status",
            "started_at",
            "ended_at",
            "is_active",
            "created_at",
            "updated_at",
        ]

    def validate(self, attrs):
        attrs = super().validate(attrs)
        request = self.context.get("request")
        clinic = getattr(request, "clinic", None)

        appointment = attrs.get("appointment")
        patient = attrs.get("patient")
        practitioner = attrs.get("practitioner")

        if self.instance is None:
            if appointment is None and (patient is None or practitioner is None):
                raise serializers.ValidationError(
                    {
                        "patient": _(
                            "Indiquez un patient et un praticien, ou un rendez-vous."
                        )
                    }
                )
            if appointment is not None:
                if patient is None:
                    attrs["patient"] = appointment.patient
                if practitioner is None:
                    attrs["practitioner"] = appointment.practitioner
                appt_patient = attrs["patient"]
                if appt_patient.pk != appointment.patient_id:
                    raise serializers.ValidationError(
                        {"appointment": _("Le rendez-vous concerne un autre patient.")}
                    )
                attrs["practitioner"] = appointment.practitioner
        else:
            # Un compte rendu se modifie ; l'affectation est figée.
            if patient is not None and patient.pk != self.instance.patient_id:
                raise serializers.ValidationError(
                    {"patient": _("Le patient d'une consultation ne peut pas changer.")}
                )
            if (
                practitioner is not None
                and practitioner.pk != self.instance.practitioner_id
            ):
                raise serializers.ValidationError(
                    {"practitioner": _("Le praticien ne peut pas changer.")}
                )

        for key in ("patient", "practitioner"):
            value = attrs.get(key)
            if value is not None and clinic is not None and value.clinic_id != clinic.pk:
                raise serializers.ValidationError(
                    {key: _("Cet enregistrement appartient à une autre clinique.")}
                )
        return attrs


class PrescriptionItemSerializer(serializers.ModelSerializer):
    """Ligne d'ordonnance."""

    class Meta:
        model = PrescriptionItem
        fields = [
            "id",
            "medication",
            "dosage",
            "frequency",
            "duration",
            "instructions",
        ]
        read_only_fields = ["id"]


class PrescriptionSerializer(serializers.ModelSerializer):
    """Ordonnance et ses lignes ; la création passe par ``add_prescription``."""

    items = PrescriptionItemSerializer(many=True)
    patient_name = serializers.CharField(source="patient.display_name", read_only=True)
    practitioner_name = serializers.CharField(
        source="practitioner.user.full_name", read_only=True
    )
    item_count = serializers.IntegerField(read_only=True)
    consultation_reference = serializers.CharField(
        source="consultation.reference", read_only=True
    )

    class Meta:
        model = Prescription
        fields = [
            "id",
            "reference",
            "consultation",
            "consultation_reference",
            "patient",
            "patient_name",
            "practitioner",
            "practitioner_name",
            "issued_at",
            "notes",
            "items",
            "item_count",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "reference",
            "patient",
            "practitioner",
            "is_active",
            "created_at",
            "updated_at",
        ]

    def validate_consultation(self, consultation):
        request = self.context.get("request")
        clinic = getattr(request, "clinic", None)
        if clinic is not None and consultation.clinic_id != clinic.pk:
            raise serializers.ValidationError(
                _("Cette consultation appartient à une autre clinique.")
            )
        return consultation

    def validate(self, attrs):
        attrs = super().validate(attrs)
        items = attrs.get("items") or []
        if not items and self.instance is None:
            raise serializers.ValidationError(
                {"items": _("Ajoutez au moins un médicament.")}
            )
        return attrs

    def create(self, validated_data):
        from .services import add_prescription

        items = validated_data.pop("items", [])
        consultation = validated_data.pop("consultation")
        request = self.context.get("request")
        user = getattr(request, "user", None)
        return add_prescription(
            consultation,
            issued_at=validated_data.get("issued_at"),
            notes=validated_data.get("notes", ""),
            items=items,
            user=user,
        )
