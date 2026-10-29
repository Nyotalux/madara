"""Permissions API de la paie (confidentielle : admin + comptabilité)."""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _
from rest_framework.permissions import BasePermission

from apps.common.permissions import IsActiveMember, IsPlatformStaff

PAYROLL_ROLES = ("ADMIN", "ACCOUNTANT")


class PayrollPermission(BasePermission):
    roles: tuple = ()
    message = _("Accès à la paie refusé pour votre rôle.")

    def has_permission(self, request, view) -> bool:
        if not IsActiveMember().has_permission(request, view):
            return False
        if IsPlatformStaff().has_permission(request, view):
            return True
        membership = getattr(request, "clinic_membership", None)
        return bool(membership and membership.role in self.roles)


class CanReadPayroll(PayrollPermission):
    """ADMIN et ACCOUNTANT de la clinique courante."""

    roles = PAYROLL_ROLES


class CanWritePayroll(PayrollPermission):
    """Bulletins, primes, paiement : mêmes rôles que la lecture."""

    roles = PAYROLL_ROLES
