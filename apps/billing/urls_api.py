"""Routage API de la facturation (DRF DefaultRouter)."""

from __future__ import annotations

from rest_framework.routers import DefaultRouter

from .api_views import InvoiceViewSet, PaymentViewSet

router = DefaultRouter()
router.register("invoices", InvoiceViewSet, basename="invoice")
router.register("payments", PaymentViewSet, basename="payment")

urlpatterns = [
    *router.urls,
]
