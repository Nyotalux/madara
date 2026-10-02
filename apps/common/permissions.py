"""Permissions DRF basées sur les rôles et le cloisonnement clinique."""

from __future__ import annotations

from rest_framework import permissions


def _role_of(user) -> str | None:
    """Rôle actif de l'utilisateur dans la clinique courante."""
    if user is None or not user.is_authenticated:
        return None
    membership = getattr(user, "active_membership", None)
    return membership.role if membership else None


class IsActiveMember(permissions.BasePermission):
    """L'utilisateur possède au moins un membership actif dans une clinique."""

    message = "Aucun accès à une clinique n'est configuré pour ce compte."

    def has_permission(self, request, view):
        user = request.user
        return bool(
            user
            and user.is_authenticated
            and (user.is_platform_staff or user.has_active_membership())
        )


class IsPlatformStaff(permissions.BasePermission):
    message = "Réservé aux administrateurs de la plateforme."

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.is_platform_staff)


class HasRole(permissions.BasePermission):
    """Autorise certains rôles dans la clinique courante.

    Utilisation : ``permission_classes = [IsActiveMember, HasRole.with_roles("DOCTOR")]``
    """

    message = "Votre rôle ne permet pas cette action."
    allowed_roles: tuple[str, ...] = ()

    @classmethod
    def with_roles(cls, *roles: str):
        return type(
            f"{cls.__name__}_{'_'.join(roles)}",
            (cls,),
            {"allowed_roles": tuple(roles)},
        )

    def has_permission(self, request, view):
        if request.user and request.user.is_authenticated and request.user.is_platform_staff:
            return True
        return _role_of(request.user) in self.allowed_roles


class IsDoctor(HasRole.with_roles("DOCTOR")):
    message = "Réservé aux médecins."


class IsNurse(HasRole.with_roles("NURSE")):
    message = "Réservé aux infirmiers."


class IsAccountant(HasRole.with_roles("ACCOUNTANT")):
    message = "Réservé au service comptabilité."


class IsClinicAdmin(HasRole.with_roles("ADMIN")):
    message = "Réservé aux administrateurs de la clinique."


class IsReception(HasRole.with_roles("RECEPTION")):
    message = "Réservé à l'accueil."


class CanReadClinicalData(permissions.BasePermission):
    """Accès aux données cliniques : médecin, infirmier, accueil, admin."""

    message = "Accès aux données cliniques réservé au personnel soignant."

    allowed_roles = ("DOCTOR", "NURSE", "ADMIN", "RECEPTION")

    def has_permission(self, request, view):
        user = request.user
        if user and user.is_authenticated and user.is_platform_staff:
            return True
        return _role_of(user) in self.allowed_roles


class IsSameClinic(permissions.BasePermission):
    """Vérifie que l'objet appartient à la clinique de la requête."""

    message = "Cet enregistrement appartient à une autre clinique."

    def has_object_permission(self, request, view, obj):
        clinic = getattr(request, "clinic", None)
        obj_clinic = getattr(obj, "clinic", None)
        if obj_clinic is None or clinic is None:
            return True
        return obj_clinic.pk == clinic.pk