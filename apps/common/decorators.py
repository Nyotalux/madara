"""Décorateurs et aides pour les vues web (exigence de clinique, rôles)."""

from __future__ import annotations

from functools import wraps

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.translation import gettext_lazy as _


def require_clinic(view):
    """Redirige vers la sélection de clinique si aucune clinique n'est active."""

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not getattr(request, "clinic", None):
            messages.warning(
                request,
                _("Sélectionnez une clinique pour continuer."),
            )
            return redirect(f"{reverse('web:login')}?next={request.get_full_path()}")
        return view(request, *args, **kwargs)

    return wrapper


def require_roles(*roles):
    """Autorise uniquement les rôles indiqués dans la clinique courante."""

    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            user = request.user
            if user.is_platform_staff:
                return view(request, *args, **kwargs)
            membership = getattr(request, "clinic_membership", None)
            if membership is None or membership.role not in roles:
                raise PermissionDenied(_("Accès refusé pour votre rôle."))
            return view(request, *args, **kwargs)

        wrapper.required_roles = roles
        return wrapper

    return decorator