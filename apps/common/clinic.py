"""Résolution de la clinique courante, partagée par le web et l'API.

L'ordre d'exécution diffère entre les deux mondes :

- **web** : ``AuthenticationMiddleware`` s'exécute avant notre middleware, donc
  ``request.user`` est déjà connu ;
- **API (JWT)** : l'utilisateur n'est authentifié que dans ``APIView.initial()``,
  donc la clinique doit être résolue au niveau de la vue.

Les deux chemins appellent ``resolve_clinic`` ; l'API passe par
``ClinicContextMixin`` qui installe également les variables de contexte.
"""

from __future__ import annotations

from django.http import Http404

from .context import current_clinic, current_user

CLINIC_HEADER = "HTTP_X_CLINIC"
CLINIC_SESSION_KEY = "madara_clinic_id"
NO_ACCESS_MESSAGE = "Aucune clinique accessible avec cet identifiant."


def resolve_clinic(request, user):
    """Détermine la clinique courante d'une requête authentifiée.

    Priorité : en-tête ``X-Clinic`` (mobile), puis session (web),
    puis le premier membership actif de l'utilisateur.
    """
    if user is None or not user.is_authenticated:
        return None

    from apps.accounts.models import Clinic

    requested = request.META.get(CLINIC_HEADER) or request.GET.get("clinic")

    if requested:
        clinic = _lookup_clinic(Clinic, requested)
        if clinic is None:
            raise Http404(NO_ACCESS_MESSAGE)
        if not user.is_platform_staff and not user.has_membership(clinic):
            raise Http404(NO_ACCESS_MESSAGE)
        _remember(request, clinic)
        return clinic

    session_clinic_id = _session_get(request, CLINIC_SESSION_KEY)
    if session_clinic_id:
        membership = (
            user.memberships.filter(
                clinic_id=session_clinic_id, is_active=True, clinic__is_active=True
            )
            .select_related("clinic")
            .first()
        )
        if membership is not None:
            return membership.clinic

    membership = user.primary_membership
    return membership.clinic if membership else None


def current_membership(user, clinic):
    """Membership actif de l'utilisateur dans la clinique donnée."""
    if user is None or not user.is_authenticated or clinic is None:
        return None
    return user.memberships.filter(clinic=clinic, is_active=True).first()


def enter_context(request, user):
    """Installe les variables de contexte ; renvoie les jetons de restauration."""
    clinic = resolve_clinic(request, user)
    request.clinic = clinic
    request.clinic_membership = current_membership(user, clinic)
    return (
        current_user.set(user if user and user.is_authenticated else None),
        current_clinic.set(clinic),
    )


def leave_context(tokens) -> None:
    user_token, clinic_token = tokens
    current_clinic.reset(clinic_token)
    current_user.reset(user_token)


class ClinicContextMixin:
    """Mixin DRF : installe la clinique courante pendant toute la requête API.

    À utiliser sur toute vue exposant des données de clinique
    (``APIView`` et ``ViewSet``). ``request.clinic`` est ensuite disponible
    dans ``get_queryset``, les permissions et les sérialiseurs.

    Avec une authentification JWT, l'utilisateur n'est connu qu'au moment de
    ``initial()`` : le contexte clinique est donc ouvert ici — avant le contrôle
    des permissions — et refermé à la sortie de ``dispatch()``.
    """

    _clinic_tokens = None

    def initial(self, request, *args, **kwargs):
        # ``request.user`` déclenche l'authentification DRF (JWT).
        self._clinic_tokens = enter_context(request, request.user)
        super().initial(request, *args, **kwargs)

    def dispatch(self, request, *args, **kwargs):
        try:
            return super().dispatch(request, *args, **kwargs)
        finally:
            if self._clinic_tokens is not None:
                leave_context(self._clinic_tokens)
                self._clinic_tokens = None


# -- utilitaires internes ---------------------------------------------------


def _lookup_clinic(Clinic, value):
    value = str(value)
    queryset = (
        Clinic.objects.filter(pk=int(value))
        if value.isdigit()
        else Clinic.objects.filter(slug=value)
    )
    return queryset.filter(is_active=True).first()


def _session_get(request, key):
    session = getattr(request, "session", None)
    if session is None:
        return None
    try:
        return session.get(key)
    except Exception:  # pragma: no cover - session sans backend configured
        return None


def _remember(request, clinic) -> None:
    session = getattr(request, "session", None)
    if session is None:
        return
    try:
        session[CLINIC_SESSION_KEY] = clinic.pk
    except Exception:  # pragma: no cover - session non inscriptible
        pass
