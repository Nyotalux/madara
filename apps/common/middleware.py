"""Middleware : publication de la clinique courante dans le contexte.

L'authentification par session étant résolue par ``AuthenticationMiddleware``,
la clinique peut être déterminée ici. Pour l'API JWT, c'est
``ClinicContextMixin`` qui s'en charge au niveau de la vue.

La résolution elle-même vit dans ``apps.common.clinic.resolve_clinic``.
"""

from __future__ import annotations

from .clinic import (
    CLINIC_HEADER,
    CLINIC_SESSION_KEY,
    current_membership,
    enter_context,
    leave_context,
)

__all__ = ["CLINIC_HEADER", "CLINIC_SESSION_KEY", "CurrentClinicMiddleware"]


class CurrentClinicMiddleware:
    """Expose ``request.clinic`` et alimente les managers de modèles."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        tokens = enter_context(request, getattr(request, "user", None))
        try:
            return self.get_response(request)
        finally:
            leave_context(tokens)

    # -- utilitaire exposé aux vues -------------------------------------

    @staticmethod
    def membership_for(request):
        return current_membership(request.user, getattr(request, "clinic", None))
