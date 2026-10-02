"""Routes web de l'agenda (namespace ``web``)."""

from __future__ import annotations

from django.urls import path

from . import views

urlpatterns = [
    path("agenda/", views.agenda, name="agenda"),
    path(
        "agenda/semaine/",
        views.agenda,
        {"vue": "semaine"},
        name="agenda-week",
    ),
    path("agenda/file/", views.waiting_room, name="waiting-room"),
    path("rendez-vous/", views.appointment_list, name="appointment-list"),
    path("rendez-vous/nouveau/", views.appointment_create, name="appointment-create"),
    path(
        "rendez-vous/creneaux/",
        views.available_slots,
        name="appointment-slots",
    ),
    path("rendez-vous/<int:pk>/", views.appointment_detail, name="appointment-detail"),
    path(
        "rendez-vous/<int:pk>/modifier/", views.appointment_edit, name="appointment-edit"
    ),
    path(
        "rendez-vous/<int:pk>/statut/",
        views.appointment_status,
        name="appointment-status",
    ),
    path(
        "rendez-vous/<int:pk>/statut/<str:status>/",
        views.appointment_quick_status,
        name="appointment-quick-status",
    ),
]
