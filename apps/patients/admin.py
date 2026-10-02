"""Administration Django des dossiers patients."""

from __future__ import annotations

from django.contrib import admin

from .models import Patient


@admin.register(Patient)
class PatientAdmin(admin.ModelAdmin):
    list_display = [
        "reference",
        "display_name",
        "birth_date",
        "phone",
        "insurer",
        "clinic",
        "is_active",
    ]
    list_filter = ["is_active", "is_insured", "gender", "blood_type", "clinic"]
    search_fields = [
        "reference",
        "first_name",
        "last_name",
        "phone",
        "cin",
        "email",
    ]
    autocomplete_fields = []
    readonly_fields = ["reference", "uuid", "created_at", "updated_at"]
    date_hierarchy = "birth_date"
    fieldsets = (
        (
            "Identité",
            {"fields": ("reference", "first_name", "last_name", "birth_date", "gender")},
        ),
        (
            "Identité administrative",
            {"fields": ("cin", "passport", "social_security")},
        ),
        (
            "Contact",
            {
                "fields": (
                    "phone",
                    "phone_secondary",
                    "email",
                    "address",
                    "city",
                    "emergency_contact_name",
                    "emergency_contact_phone",
                )
            },
        ),
        (
            "Couverture sociale",
            {"fields": ("is_insured", "insurer", "insurance_number", "insurance_expiry")},
        ),
        (
            "Repères médicaux",
            {
                "fields": (
                    "blood_type",
                    "height_cm",
                    "weight_kg",
                    "allergies",
                    "chronic_conditions",
                    "current_medication",
                    "is_pregnant",
                )
            },
        ),
        ("Préférences", {"fields": ("preferred_language",)}),
        (
            "Suivi",
            {
                "fields": (
                    "notes",
                    "is_active",
                    "deleted_at",
                    "deletion_reason",
                    "created_by",
                    "created_at",
                    "updated_at",
                    "uuid",
                )
            },
        ),
    )

    @admin.display(description="Clinique", ordering="clinic__name")
    def clinic_name(self, obj):
        return obj.clinic.name

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("clinic")
