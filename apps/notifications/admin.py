"""Administration Django des notifications (jalon 5)."""

from __future__ import annotations

from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "channel",
        "category",
        "status_badge",
        "display_recipient",
        "scheduled_for",
        "sent_at",
        "read_at",
        "clinic",
    )
    list_filter = ("channel", "category", "status", "clinic")
    search_fields = (
        "title",
        "email",
        "phone",
        "recipient__first_name",
        "recipient__last_name",
        "recipient__email",
        "patient__first_name",
        "patient__last_name",
    )
    readonly_fields = ("created_at", "updated_at", "created_by")
    list_select_related = ("recipient", "patient", "clinic")
    raw_id_fields = ("recipient", "patient", "appointment")
    date_hierarchy = "created_at"

    @admin.display(description=_("état"), ordering="status")
    def status_badge(self, obj):
        return obj.get_status_display()
