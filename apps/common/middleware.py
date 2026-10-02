"""Middleware : résolution de la clinique courante.

Ordre de résolution :
  1. en-tête ``X-Clinic`` (slug ou ID) — utilisé par les applications mobiles ;
  2. session ``clinic_id`` (bascule de clinique dans l'interface web) ;
  3. le membership actif par défaut de l'utilisateur.

Le résultat est exposé sur ``request.clinic`` et injecté dans un ``ContextVar``
afin que les managers de modèles filtrent automatiquement.
"""

from __future__ import annotations

from django.http import Http404

from .context import current_clinic, current_user

CLINIC_HEADER = "HTTP_X_CLINIC"
CLINIC_SESSION_KEY = "madara_clinic_id"


class CurrentClinicMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        authenticated = bool(user and user.is_authenticated)

        current_user.set(user if authenticated else None)

        clinic = None
        if authenticated:
            clinic = self._resolve(request, user)

        request.clinic = clinic
        request.clinic_membership = self._membership(user, clinic)
        current_clinic.set(clinic)
        try:
            return self.get_response(request)
        finally:
            current_clinic.reset()
            current_user.reset()

    # -- interne ---------------------------------------------------------

    def _membership(self, user, clinic):
        if user is None or not user.is_authenticated or clinic is None:
            return None
        return user.memberships.filter(clinic=clinic, is_active=True).first()

    def _resolve(self, request, user):
        from apps.accounts.models import Clinic

        requested = request.META.get(CLINIC_HEADER) or request.GET.get("clinic")

        if requested and not user.is_platform_staff:
            clinic = self._lookup(Clinic, requested)
            if clinic is None or not user.has_membership(clinic):
                raise Http404(_NO_ACCESS_MESSAGE)
            request.session[CLINIC_SESSION_KEY] = clinic.pk
            return clinic

        if requested and user.is_platform_staff:
            clinic = self._lookup(Clinic, requested)
            if clinic is not None:
                request.session[CLINIC_SESSION_KEY] = clinic.pk
                return clinic

        session_clinic_id = request.session.get(CLINIC_SESSION_KEY)
        if session_clinic_id:
            clinic = (
                user.memberships.filter(
                    clinic_id=session_clinic_id, is_active=True
                )
                .select_related("clinic")
                .values_list("clinic", flat=True)
                .first()
            )
            if clinic is not None:
                return clinic

        membership = user.primary_membership
        return membership.clinic if membership else None

    @staticmethod
    def _lookup(Clinic, value):
        query = (
            Clinic.objects.filter(slug=value)
            if not str(value).isdigit()
            else Clinic.objects.filter(pk=int(value))
        )
        return query.filter(is_active=True).first()


_NO_ACCESS_MESSAGE = "Aucune clinique accessible avec cet identifiant."