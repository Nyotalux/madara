"""Sérialiseurs DRF des notifications."""

from __future__ import annotations

from rest_framework import serializers

from .models import Notification


class NotificationSerializer(serializers.ModelSerializer):
    """Notification de l'utilisateur connecté (lecture et marquage « lu »)."""

    patient_name = serializers.CharField(source="patient.display_name", read_only=True)
    appointment_reference = serializers.CharField(
        source="appointment.reference", read_only=True
    )
    is_unread = serializers.BooleanField(read_only=True)

    class Meta:
        model = Notification
        fields = [
            "id",
            "channel",
            "category",
            "status",
            "title",
            "body",
            "is_unread",
            "patient_name",
            "appointment_reference",
            "scheduled_for",
            "sent_at",
            "read_at",
            "created_at",
        ]
        read_only_fields = fields
