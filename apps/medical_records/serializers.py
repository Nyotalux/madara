"""Sérialiseurs DRF du dossier médical (antécédents, documents, partages)."""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from .models import MedicalDocument, MedicalRecord, MedicalRecordShare


class MedicalRecordSerializer(serializers.ModelSerializer):
    """Dossier médical complet, avec les compteurs de pièces et de partages."""

    patient = serializers.PrimaryKeyRelatedField(read_only=True)
    patient_name = serializers.CharField(source="patient.display_name", read_only=True)
    patient_reference = serializers.CharField(source="patient.reference", read_only=True)
    document_count = serializers.IntegerField(read_only=True)
    has_active_share = serializers.BooleanField(read_only=True)

    class Meta:
        model = MedicalRecord
        fields = [
            "id",
            "reference",
            "patient",
            "patient_reference",
            "patient_name",
            "family_history",
            "medical_history",
            "surgical_history",
            "gynecological_history",
            "vaccinations",
            "lifestyle",
            "disabilities",
            "notes",
            "last_reviewed_at",
            "reviewed_by",
            "document_count",
            "has_active_share",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "reference",
            "patient",
            "reviewed_by",
            "last_reviewed_at",
            "is_active",
            "created_at",
            "updated_at",
        ]


class MedicalRecordListSerializer(serializers.ModelSerializer):
    """Version compacte pour les listes."""

    patient_name = serializers.CharField(source="patient.display_name", read_only=True)
    patient_reference = serializers.CharField(source="patient.reference", read_only=True)
    document_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = MedicalRecord
        fields = [
            "id",
            "reference",
            "patient",
            "patient_reference",
            "patient_name",
            "document_count",
            "last_reviewed_at",
            "is_active",
        ]
        read_only_fields = fields


class MedicalDocumentSerializer(serializers.ModelSerializer):
    """Pièce du dossier ; le dépôt passe par ``services.add_document``."""

    record = serializers.PrimaryKeyRelatedField(queryset=MedicalRecord.objects.all())
    filename = serializers.CharField(read_only=True)
    extension = serializers.CharField(read_only=True)
    uploaded_by = serializers.SerializerMethodField()

    class Meta:
        model = MedicalDocument
        fields = [
            "id",
            "record",
            "category",
            "title",
            "file",
            "document_date",
            "description",
            "filename",
            "extension",
            "uploaded_by",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "is_active", "created_at", "updated_at"]

    def get_uploaded_by(self, obj) -> str:
        return obj.created_by.full_name if obj.created_by_id else ""

    def validate_record(self, record):
        request = self.context.get("request")
        clinic = getattr(request, "clinic", None)
        if clinic is not None and record.clinic_id != clinic.pk:
            raise serializers.ValidationError(
                _("Ce dossier appartient à une autre clinique.")
            )
        return record

    def create(self, validated_data):
        from .services import add_document

        request = self.context.get("request")
        user = getattr(request, "user", None)
        record = validated_data.pop("record")
        return add_document(
            record,
            category=validated_data.get("category", MedicalDocument.Category.OTHER),
            title=validated_data["title"],
            file=validated_data["file"],
            document_date=validated_data.get("document_date"),
            description=validated_data.get("description", ""),
            user=user,
        )


class MedicalRecordShareSerializer(serializers.ModelSerializer):
    """Lien de partage temporaire ; ``days`` alimente ``create_share``."""

    record = serializers.PrimaryKeyRelatedField(queryset=MedicalRecord.objects.all())
    days = serializers.IntegerField(write_only=True, min_value=1, required=False)
    status = serializers.CharField(source="status_label", read_only=True)
    is_valid = serializers.BooleanField(read_only=True)

    class Meta:
        model = MedicalRecordShare
        fields = [
            "id",
            "record",
            "token",
            "shared_with",
            "note",
            "days",
            "expires_at",
            "revoked_at",
            "status",
            "is_valid",
            "created_at",
        ]
        read_only_fields = [
            "id",
            "token",
            "expires_at",
            "revoked_at",
            "created_at",
        ]

    def validate_record(self, record):
        request = self.context.get("request")
        clinic = getattr(request, "clinic", None)
        if clinic is not None and record.clinic_id != clinic.pk:
            raise serializers.ValidationError(
                _("Ce dossier appartient à une autre clinique.")
            )
        return record

    def create(self, validated_data):
        from .services import create_share

        request = self.context.get("request")
        user = getattr(request, "user", None)
        record = validated_data.pop("record")
        days = validated_data.pop("days", None)
        return create_share(
            record,
            days=days,
            shared_with=validated_data.get("shared_with", ""),
            note=validated_data.get("note", ""),
            user=user,
        )
