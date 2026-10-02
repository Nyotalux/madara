"""Administration Django des rendez-vous."""

from __future__ import annotations

from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import Appointment, AppointmentCancellation


class AppointmentCancellationInline(admin.TabularInline):
    model = AppointmentCancellation
    extra = 0
    fields = ("reason", "cancelled_by", "created_at")
    readonly_fields = ("created_at",)
    verbose_name = _("annulation")
    verbose_name_plural = _("annulations")


@admin.register(Appointment)
class AppointmentAdmin(admin.ModelAdmin):
    list_display = (
        "reference",
        "patient",
        "practitioner",
        "local_date",
        "start_time_display",
        "kind",
        "status_badge",
        "is_active",
    )
    list_filter = ("status", "kind", "start_at", "is_active", "clinic")
    search_fields = (
        "reference",
        "patient__first_name",
        "patient__last_name",
        "patient__phone",
        "reason",
    )
    date_hierarchy = "start_at"
    autocomplete_fields = ("patient", "practitioner")
    inlines = [AppointmentCancellationInline]
    readonly_fields = ("reference", "created_at", "updated_at", "created_by")
    list_select_related = ("patient", "practitioner", "clinic")
    fieldsets = (
        (None, {"fields": ("reference", "patient", "practitioner", "kind", "reason")}),
        (
            _("Créneau"),
            {
                "fields": ("start_at", "end_at", "status"),
                "description": _("Les chevauchements sont contrôlés à l'enregistrement."),
            },
        ),
        (
            _("Suivi"),
            {
                "fields": (
                    "cancelled_at",
                    "cancellation_reason",
                    "reminder_sent_at",
                    "notes",
                )
            },
        ),
        (_("Traçabilité"), {"fields": ("created_by", "created_at", "updated_at")}),
    )

    @admin.display(description=_("début"), ordering="start_at")
    def start_time_display(self, obj):
        return obj.display_time

    @admin.display(description=_("statut"), ordering="status")
    def status_badge(self, obj):
        return obj.get_status_display()

    @admin.display(description=_("date"), ordering="start_at")
    def local_date(self, obj):
        return obj.display_date

    @admin.action(description=_("Confirmer les rendez-vous sélectionnés"))
    def confirm_selected(self, request, queryset):
        confirmed = 0
        for appointment in queryset.exclude(status=Appointment.Status.DONE):
            if appointment.can_transition_to(Appointment.Status.CONFIRMED):
                appointment.transition_to(Appointment.Status.CONFIRMED, user=request.user)
                confirmed += 1
        self.message_user(
            request, _("%(count)s rendez-vous confirmés.") % {"count": confirmed}
        )


@admin.register(AppointmentCancellation)
class AppointmentCancellationAdmin(admin.ModelAdmin):
    list_display = ("appointment", "reason", "cancelled_by", "created_at")
    list_filter = ("created_at",)
    search_fields = ("appointment__reference", "reason")
    raw_id_fields = ("appointment", "cancelled_by")
