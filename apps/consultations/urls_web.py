"""Routes web des consultations et ordonnances (namespace ``web``)."""

from __future__ import annotations

from django.urls import path

from . import views

urlpatterns = [
    path("consultations/", views.consultation_list, name="consultation-list"),
    path("consultations/nouveau/", views.consultation_start, name="consultation-create"),
    path(
        "consultations/<int:pk>/",
        views.consultation_detail,
        name="consultation-detail",
    ),
    path(
        "consultations/<int:pk>/modifier/",
        views.consultation_edit,
        name="consultation-edit",
    ),
    path(
        "consultations/<int:pk>/terminer/",
        views.consultation_complete,
        name="consultation-complete",
    ),
    path(
        "consultations/<int:pk>/annuler/",
        views.consultation_cancel,
        name="consultation-cancel",
    ),
    path(
        "consultations/<int:pk>/ordonnance/",
        views.prescription_create,
        name="prescription-create",
    ),
    path(
        "ordonnances/<int:pk>/imprimer/",
        views.prescription_print,
        name="prescription-print",
    ),
    # Démarrage depuis la salle d'attente : le rendez-vous porte la consultation.
    path(
        "rendez-vous/<int:pk>/consultation/",
        views.consultation_start,
        name="consultation-start",
    ),
]
