"""Administration Django des consultations et ordonnances."""

from __future__ import annotations

from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import Consultation, Prescription, PrescriptionItem, VitalSignsMixin


class PrescriptionItemInline(admin.TabularInline):
    model = PrescriptionItem
    extra = 1
    fields = ("medication", "dosage", "frequency", "duration", "instructions")


@admin.register(Prescription)
class PrescriptionAdmin(admin.ModelAdmin):
    list_display = ("reference", "patient", "practitioner", "issued_at", "item_count")
    list_filter = ("issued_at", "clinic")
    search_fields = ("reference", "patient__first_name", "patient__last_name")
    date_hierarchy = "issued_at"
    autocomplete_fields = ("patient", "practitioner", "consultation")
    readonly_fields = ("reference", "created_at", "updated_at", "created_by")
    list_select_related = ("patient", "practitioner", "clinic")
    inlines = [PrescriptionItemInline]

    @admin.display(description=_("lignes"))
    def item_count(self, obj):
        return obj.item_count


@admin.register(PrescriptionItem)
class PrescriptionItemAdmin(admin.ModelAdmin):
    list_display = ("medication", "prescription", "dosage", "duration")
    search_fields = ("medication", "prescription__reference")
    raw_id_fields = ("prescription",)


class PrescriptionInline(admin.TabularInline):
    model = Prescription
    extra = 0
    fields = ("reference", "issued_at", "notes")
    readonly_fields = ("reference",)
    show_change_link = True


@admin.register(Consultation)
class ConsultationAdmin(admin.ModelAdmin):
    list_display = (
        "reference",
        "patient",
        "practitioner",
        "started_at",
        "kind",
        "status_badge",
        "is_active",
    )
    list_filter = ("status", "kind", "started_at", "is_active", "clinic")
    search_fields = (
        "reference",
        "patient__first_name",
        "patient__last_name",
        "patient__reference",
        "chief_complaint",
        "diagnosis",
    )
    date_hierarchy = "started_at"
    autocomplete_fields = ("patient", "practitioner", "appointment")
    readonly_fields = ("reference", "created_at", "updated_at", "created_by")
    list_select_related = ("patient", "practitioner", "clinic")
    inlines = [PrescriptionInline]
    fieldsets = (
        (None, {"fields": ("reference", "patient", "practitioner", "appointment")}),
        (_("Déroulement"), {"fields": ("status", "kind", "started_at", "ended_at")}),
        (
            _("Compte rendu"),
            {
                "fields": (
                    "chief_complaint",
                    "history",
                    "examination",
                    "diagnosis",
                    "treatment",
                    "follow_up_date",
                )
            },
        ),
        (
            _("Constantes"),
            {
                "classes": ("collapse",),
                "fields": VitalSignsMixin.VITAL_FIELDS,
            },
        ),
        (_("Suivi"), {"fields": ("notes",)}),
        (_("Traçabilité"), {"fields": ("created_by", "created_at", "updated_at")}),
    )

    @admin.display(description=_("statut"), ordering="status")
    def status_badge(self, obj):
        return obj.get_status_display()
