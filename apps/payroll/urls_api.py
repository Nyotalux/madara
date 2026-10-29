"""Routage API de la paie (DRF DefaultRouter)."""

from __future__ import annotations

from rest_framework.routers import DefaultRouter

from .api_views import BonusViewSet, PayslipViewSet

router = DefaultRouter()
router.register("payslips", PayslipViewSet, basename="payslip")
router.register("bonuses", BonusViewSet, basename="bonus")

urlpatterns = [
    *router.urls,
]
