"""Administration Django des comptes, cliniques et rôles."""

from __future__ import annotations

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.utils.translation import gettext_lazy as _

from .models import (
    BlockedSlot,
    Clinic,
    Membership,
    OpeningHour,
    PractitionerProfile,
    Specialty,
    User,
)


@admin.register(Clinic)
class ClinicAdmin(admin.ModelAdmin):
    list_display = ["name", "city", "phone", "currency", "tax_rate", "is_active"]
    list_filter = ["is_active", "country", "currency"]
    search_fields = ["name", "legal_name", "city", "phone", "email"]
    prepopulated_fields = {"slug": ("name",)}
    fieldsets = (
        (_("Identité"), {"fields": ("name", "legal_name", "slug", "logo")}),
        (
            _("Coordonnées"),
            {
                "fields": (
                    "address",
                    "city",
                    "country",
                    "phone",
                    "emergency_phone",
                    "email",
                    "website",
                )
            },
        ),
        (
            _("Administratif"),
            {"fields": ("tax_identifier", "license_number", "tax_rate")},
        ),
        (
            _("Paramètres"),
            {
                "fields": (
                    ("currency", "timezone", "language"),
                    ("appointment_slot_minutes", "dunning_days"),
                    "is_active",
                )
            },
        ),
    )


class PractitionerProfileInline(admin.StackedInline):
    model = PractitionerProfile
    can_delete = False
    extra = 0


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = [
        "user",
        "clinic",
        "role",
        "employee_number",
        "hire_date",
        "is_active",
    ]
    list_filter = ["role", "is_active", "clinic"]
    search_fields = [
        "user__email",
        "user__first_name",
        "user__last_name",
        "employee_number",
    ]
    autocomplete_fields = ["user", "clinic"]
    inlines = [PractitionerProfileInline]
    fieldsets = (
        (None, {"fields": ("user", "clinic", "role", "is_active")}),
        (_("Contrat"), {"fields": ("employee_number", "hire_date", "contract_end_date")}),
        (_("Rémunération"), {"fields": ("base_salary",)}),
        (_("Divers"), {"fields": ("notes",)}),
    )


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    list_display = ["email", "full_name", "is_platform_staff", "is_active", "date_joined"]
    list_filter = ["is_platform_staff", "is_staff", "is_superuser", "is_active"]
    search_fields = ["email", "first_name", "last_name"]
    ordering = ["-date_joined"]
    readonly_fields = ["date_joined", "last_login"]
    filter_horizontal = ["groups", "user_permissions"]
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        (_("Identité"), {"fields": ("first_name", "last_name", "phone", "avatar", "locale")}),
        (
            _("Plateforme"),
            {"fields": ("is_platform_staff", "is_staff", "is_superuser", "is_active")},
        ),
        (_("Notifications"), {"fields": ("notification_email",)}),
        (
            _("Permissions"),
            {"fields": ("groups", "user_permissions"), "classes": ("collapse",)},
        ),
        (_("Dates"), {"fields": ("last_login", "date_joined"), "classes": ("collapse",)}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "password1", "password2", "is_platform_staff"),
            },
        ),
    )

    def get_search_results(self, request, queryset, search_term):
        queryset, may_hide_duplicates = super().get_search_results(
            request, queryset, search_term
        )
        if not search_term:
            return queryset, may_hide_duplicates
        return queryset, False


@admin.register(PractitionerProfile)
class PractitionerProfileAdmin(admin.ModelAdmin):
    list_display = [
        "membership",
        "profession",
        "specialty",
        "license_number",
        "years_of_experience",
    ]
    list_filter = ["profession", "specialty"]
    search_fields = ["membership__user__email", "membership__user__last_name", "license_number"]
    autocomplete_fields = ["membership", "specialty"]


@admin.register(Specialty)
class SpecialtyAdmin(admin.ModelAdmin):
    list_display = ["name", "code"]
    search_fields = ["name", "code"]


@admin.register(OpeningHour)
class OpeningHourAdmin(admin.ModelAdmin):
    list_display = ["membership", "weekday", "start_time", "end_time", "is_closed"]
    list_filter = ["weekday", "is_closed"]


@admin.register(BlockedSlot)
class BlockedSlotAdmin(admin.ModelAdmin):
    list_display = ["membership", "start_at", "end_at", "reason"]
    list_filter = ["membership__role"]
    date_hierarchy = "start_at"