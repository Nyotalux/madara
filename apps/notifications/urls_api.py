"""Routage API des notifications (DRF DefaultRouter)."""

from __future__ import annotations

from django.urls import path
from rest_framework.routers import DefaultRouter

from .api_views import NotificationViewSet, PushSubscriptionAPI, WebPushStatusAPI

router = DefaultRouter()
router.register("notifications", NotificationViewSet, basename="notification")

urlpatterns = [
    *router.urls,
    path("webpush/", PushSubscriptionAPI.as_view(), name="webpush-subscriptions"),
    path("webpush/cle/", WebPushStatusAPI.as_view(), name="webpush-key"),
]
