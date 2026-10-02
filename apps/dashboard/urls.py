"""Routes web du tableau de bord (namespace ``web``)."""

from __future__ import annotations

from django.urls import path

from . import views

app_name = "web"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
]