"""Permissions API du stock.

Le stock est visible par toute l'équipe (les praticiens vérifient la
disponibilité d'un médicament) ; la gestion des produits et des mouvements
appartient à l'administration, l'accueil et la comptabilité.
"""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _
from rest_framework.permissions import BasePermission

from apps.common.permissions import IsActiveMember, IsPlatformStaff

INVENTORY_READ_ROLES = ("ADMIN", "RECEPTION", "DOCTOR", "NURSE", "ACCOUNTANT")
INVENTORY_WRITE_ROLES = ("ADMIN", "RECEPTION", "ACCOUNTANT")


class _RolePermission(BasePermission):
    roles: tuple = ()
    message = _("Accès au stock refusé pour votre rôle.")

    def has_permission(self, request, view) -> bool:
        if not IsActiveMember().has_permission(request, view):
            return False
        if IsPlatformStaff().has_permission(request, view):
            return True
        membership = getattr(request, "clinic_membership", None)
        return bool(membership and membership.role in self.roles)


class CanReadInventory(_RolePermission):
    """Toute l'équipe de la clinique courante."""

    roles = INVENTORY_READ_ROLES


class CanWriteInventory(_RolePermission):
    """Produits et mouvements : administration, accueil, comptabilité."""

    roles = INVENTORY_WRITE_ROLES
