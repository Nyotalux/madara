"""Routes web de l'app ``patients`` (namespace ``web``)."""

from __future__ import annotations

from django.urls import path

from . import views

urlpatterns = [
    path("patients/", views.patient_list, name="patient-list"),
    path("patients/nouveau/", views.patient_create, name="patient-create"),
    path("patients/recherche/", views.patient_autocomplete, name="patient-autocomplete"),
    path("patients/statistiques/", views.patient_stats, name="patient-stats"),
    path("patients/<int:pk>/", views.patient_detail, name="patient-detail"),
    path("patients/<int:pk>/modifier/", views.patient_edit, name="patient-edit"),
    path("patients/<int:pk>/archiver/", views.patient_archive, name="patient-archive"),
    path("patients/<int:pk>/restaurer/", views.patient_restore, name="patient-restore"),
    path("patients/<int:pk>/fiche/", views.patient_print, name="patient-print"),
    path("patients/<int:pk>/resume/", views.patient_summary, name="patient-summary"),
]
