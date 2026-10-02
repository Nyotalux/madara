"""Ressources API de l'app ``patients`` (préfixe ``/api/v1/``)."""

from __future__ import annotations

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .api_views import PatientViewSet

router = DefaultRouter()
router.register("patients", PatientViewSet, basename="patient")

urlpatterns = [
    path("", include(router.urls)),
]
