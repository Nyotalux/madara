"""Contexte de gabarit : clinique courante, rôle, navigation."""

from __future__ import annotations

from django.conf import settings


def _unread_count(user) -> int:
    """Nombre de notifications non lues (l'app notifications arrive au jalon 3)."""
    if user is None or not user.is_authenticated:
        return 0
    if not hasattr(user, "notifications"):
        return 0
    return user.notifications.filter(read_at__isnull=True).count()


def site_context(request):
    user = getattr(request, "user", None)
    clinic = getattr(request, "clinic", None)
    membership = getattr(request, "clinic_membership", None)

    return {
        "app_name": "Madara",
        "app_version": "1.0.0",
        "current_clinic": clinic,
        "current_membership": membership,
        "current_role": (
            membership.get_role_display() if membership is not None else None
        ),
        "available_clinics": (
            user.clinics.all() if user is not None and user.is_authenticated else []
        ),
        "unread_notifications": _unread_count(user),
        "debug": settings.DEBUG,
        "media_url": settings.MEDIA_URL,
    }