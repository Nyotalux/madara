"""Réglages DRF partagés : pagination, filtres de base, gestion d'erreurs."""

from __future__ import annotations

from django.core.exceptions import (
    PermissionDenied,
    ValidationError as DjangoValidationError,
)
from django.db import IntegrityError
from django.http import Http404
from django.utils.translation import gettext as _
from rest_framework import status
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler


class DefaultPagination(PageNumberPagination):
    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 200


class LargePagination(DefaultPagination):
    page_size = 100
    max_page_size = 500


class ClinicScopedFilterSetMixin:
    """Filtre par clinique courante quand le paramètre n'est pas fourni."""

    clinic_field = "clinic"

    def filter_queryset(self, queryset):
        clinic = getattr(self.request, "clinic", None)
        if clinic is not None and self.clinic_field not in self.data:
            queryset = queryset.filter(**{self.clinic_field: clinic})
        return super().filter_queryset(queryset)


def madara_exception_handler(exc, context):
    """Normalise les erreurs de l'API et masque les détails internes en production."""
    if isinstance(exc, DjangoValidationError):
        exc = ValidationError(detail=getattr(exc, "messages", [str(exc)]))
    elif isinstance(exc, Http404):
        exc = APIException(detail=_("Ressource introuvable."))
        exc.status_code = status.HTTP_404_NOT_FOUND
    elif isinstance(exc, PermissionDenied):
        exc = APIException(detail=_("Accès refusé."))
        exc.status_code = status.HTTP_403_FORBIDDEN
    elif isinstance(exc, IntegrityError):
        exc = APIException(
            detail=_(
                "Opération impossible : cet enregistrement existe déjà ou viole une contrainte."
            )
        )
        exc.status_code = status.HTTP_409_CONFLICT

    response = drf_exception_handler(exc, context)

    if response is None:
        # Exception non gérée : journalisée par Django, on masque le détail.
        return None

    detail = response.data
    if isinstance(detail, dict) and set(detail.keys()) == {"detail"}:
        message = detail["detail"]
    else:
        message = _("Une erreur est survenue.")

    response.data = {
        "error": {
            "code": getattr(exc, "default_code", "error"),
            "message": message,
            "details": detail
            if not (isinstance(detail, dict) and set(detail.keys()) == {"detail"})
            else None,
        }
    }
    return response


def ok(data=None, **extra):
    payload = {"ok": True}
    if data is not None:
        payload["data"] = data
    payload.update(extra)
    return Response(payload, status=status.HTTP_200_OK)
