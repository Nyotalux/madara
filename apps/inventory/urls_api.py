"""Routage API du stock (DRF DefaultRouter)."""

from __future__ import annotations

from rest_framework.routers import DefaultRouter

from .api_views import ProductViewSet, StockMovementViewSet

router = DefaultRouter()
router.register("products", ProductViewSet, basename="product")
router.register("stock-movements", StockMovementViewSet, basename="stock-movement")

urlpatterns = [
    *router.urls,
]
