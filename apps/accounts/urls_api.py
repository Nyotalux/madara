"""Ressources API de l'app ``accounts`` (préfixe ``/api/v1/``)."""

from __future__ import annotations

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .api_views import (
    BlockedSlotViewSet,
    ClinicViewSet,
    MeView,
    MembershipViewSet,
    OpeningHourViewSet,
    SpecialtyViewSet,
    StaffViewSet,
    SwitchClinicView,
)

app_name = "accounts_api"

router = DefaultRouter()
router.register("clinics", ClinicViewSet, basename="clinic")
router.register("memberships", MembershipViewSet, basename="membership")
router.register("staff", StaffViewSet, basename="staff")
router.register("specialties", SpecialtyViewSet, basename="specialty")
router.register("opening-hours", OpeningHourViewSet, basename="opening-hour")
router.register("blocked-slots", BlockedSlotViewSet, basename="blocked-slot")

urlpatterns = [
    path("me/", MeView.as_view(), name="me"),
    path("me/switch-clinic/", SwitchClinicView.as_view(), name="switch-clinic"),
    path("", include(router.urls)),
]