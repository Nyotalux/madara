"""Administration Django du dossier médical."""

from __future__ import annotations

from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import MedicalDocument, MedicalRecord, MedicalRecordShare


class MedicalDocumentInline(admin.TabularInline):
    model = MedicalDocument
    extra = 0
    fields = ("category", "title", "document_date", "file", "is_active")
    readonly_fields = ("created_at",)
    show_change_link = True


class MedicalRecordShareInline(admin.TabularInline):
    model = MedicalRecordShare
    extra = 0
    fields = ("shared_with", "expires_at", "revoked_at", "created_by")
    readonly_fields = ("expires_at", "revoked_at", "created_by")


@admin.register(MedicalRecord)
class MedicalRecordAdmin(admin.ModelAdmin):
    list_display = (
        "reference",
        "patient",
        "document_count",
        "has_active_share",
        "last_reviewed_at",
        "is_active",
    )
    list_filter = ("is_active", "clinic")
    search_fields = (
        "reference",
        "patient__first_name",
        "patient__last_name",
        "patient__reference",
    )
    autocomplete_fields = ("patient", "reviewed_by")
    readonly_fields = ("reference", "created_at", "updated_at", "created_by")
    list_select_related = ("patient", "clinic")
    inlines = [MedicalDocumentInline, MedicalRecordShareInline]

    @admin.display(description=_("documents"))
    def document_count(self, obj):
        return obj.document_count

    @admin.display(boolean=True, description=_("partage actif"))
    def has_active_share(self, obj):
        return obj.has_active_share


@admin.register(MedicalDocument)
class MedicalDocumentAdmin(admin.ModelAdmin):
    list_display = ("title", "category", "record", "document_date", "is_active")
    list_filter = ("category", "document_date", "is_active", "clinic")
    search_fields = ("title", "record__reference", "record__patient__last_name")
    date_hierarchy = "document_date"
    autocomplete_fields = ("record",)
    readonly_fields = ("created_at", "updated_at", "created_by")
    list_select_related = ("record", "record__patient")


@admin.register(MedicalRecordShare)
class MedicalRecordShareAdmin(admin.ModelAdmin):
    list_display = (
        "record",
        "shared_with",
        "status_label",
        "expires_at",
        "revoked_at",
    )
    list_filter = ("revoked_at", "expires_at", "clinic")
    search_fields = ("record__reference", "shared_with", "token")
    readonly_fields = ("token", "created_at", "updated_at", "created_by")
    list_select_related = ("record",)

    @admin.display(description=_("statut"))
    def status_label(self, obj):
        return obj.status_label
