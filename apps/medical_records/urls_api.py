"""Ressources API de l'app ``medical_records`` (préfixe ``/api/v1/``)."""

from __future__ import annotations

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .api_views import (
    MedicalDocumentViewSet,
    MedicalRecordShareViewSet,
    MedicalRecordViewSet,
)

router = DefaultRouter()
router.register("medical-records", MedicalRecordViewSet, basename="medical-record")
router.register("medical-documents", MedicalDocumentViewSet, basename="medical-document")
router.register("medical-shares", MedicalRecordShareViewSet, basename="medical-share")

urlpatterns = [
    path("", include(router.urls)),
]
