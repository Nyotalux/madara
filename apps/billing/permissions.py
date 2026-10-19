"""Permissions de la facturation (les praticiens y sont exclus).

Les dossiers médicaux sont fermés à la comptabilité ; inversement, la
facturation est fermée aux praticiens : lecture et écriture appartiennent à
l'administration, l'accueil et la comptabilité. L'annulation est réservée à
l'administration et à la comptabilité.
"""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _
from rest_framework.permissions import BasePermission

from apps.common.permissions import IsActiveMember, IsPlatformStaff


class CanReadBilling(BasePermission):
    """ADMIN, RECEPTION et ACCOUNTANT de la clinique courante."""

    message = _("Accès à la facturation refusé pour votre rôle.")

    def has_permission(self, request, view) -> bool:
        if not IsActiveMember().has_permission(request, view):
            return False
        if IsPlatformStaff().has_permission(request, view):
            return True
        membership = getattr(request, "clinic_membership", None)
        return bool(
            membership and membership.role in ("ADMIN", "RECEPTION", "ACCOUNTANT")
        )


class CanWriteBilling(CanReadBilling):
    """Facturer et encaisser : mêmes rôles que la lecture."""


class CanCancelInvoice(CanReadBilling):
    """Annulation de facture : administration et comptabilité uniquement."""

    message = _("Seuls l'administration et la comptabilité peuvent annuler une facture.")

    def has_permission(self, request, view) -> bool:
        if not super().has_permission(request, view):
            return False
        if IsPlatformStaff().has_permission(request, view):
            return True
        membership = getattr(request, "clinic_membership", None)
        return bool(membership and membership.role in ("ADMIN", "ACCOUNTANT"))
