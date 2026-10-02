"""Routes web de l'app ``accounts`` (namespace ``web``)."""

from __future__ import annotations

from django.urls import path

from . import views

urlpatterns = [
    path("connexion/", views.MadaraLoginView.as_view(), name="login"),
    path("deconnexion/", views.MadaraLogoutView.as_view(), name="logout"),
    path("clinique/switch/", views.switch_clinic, name="switch-clinic"),
    path("cliniques/<slug:slug>/", views.clinic_detail, name="clinic-detail"),
    path("profil/", views.profile, name="profile"),
    path("profil/mot-de-passe/", views.change_password, name="change-password"),
    path("personnel/", views.staff_list, name="staff-list"),
    path("personnel/nouveau/", views.staff_create, name="staff-create"),
    path("personnel/<int:pk>/", views.staff_detail, name="staff-detail"),
    path("personnel/<int:pk>/modifier/", views.staff_edit, name="staff-edit"),
]
