"""Authentification API (inclus sous ``/api/v1/auth/``)."""

from __future__ import annotations

from django.urls import path

from .api_auth import LogoutView, RefreshTokenView, TokenObtainPairWithClinicView

app_name = "auth"

urlpatterns = [
    path("login/", TokenObtainPairWithClinicView.as_view(), name="login"),
    path("refresh/", RefreshTokenView.as_view(), name="refresh"),
    path("logout/", LogoutView.as_view(), name="logout"),
]