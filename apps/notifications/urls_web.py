"""Routes web des notifications (namespace ``web``)."""

from __future__ import annotations

from django.urls import path

from . import views

urlpatterns = [
    path("notifications/", views.notification_list, name="notification-list"),
    path(
        "notifications/tout-lire/",
        views.notification_mark_all_read,
        name="notification-read-all",
    ),
    path(
        "notifications/<int:pk>/lue/",
        views.notification_read,
        name="notification-read",
    ),
    path("sw.js", views.service_worker, name="sw"),
]
