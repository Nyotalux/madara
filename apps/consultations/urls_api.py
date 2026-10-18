"""Ressources API de l'app ``consultations`` (préfixe ``/api/v1/``)."""

from __future__ import annotations

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .api_views import ConsultationViewSet, PrescriptionViewSet

router = DefaultRouter()
router.register("consultations", ConsultationViewSet, basename="consultation")
router.register("prescriptions", PrescriptionViewSet, basename="prescription")

urlpatterns = [
    path("", include(router.urls)),
]
