"""Validateurs réutilisables."""

from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

EXTENSION_ERROR = _("Type de fichier non autorisé : %(extensions)s")
SIZE_ERROR = _("Fichier trop volumineux : maximum %(size)s Mo.")


def validate_document_file(uploaded_file):
    """Vérifie extension et taille des pièces jointes (scans, ordonnances...)."""
    name = getattr(uploaded_file, "name", "") or ""
    extension = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    allowed = settings.UPLOAD_ALLOWED_EXTENSIONS

    if extension not in allowed:
        raise ValidationError(EXTENSION_ERROR, params={"extensions": ", ".join(allowed)})

    max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    size = getattr(uploaded_file, "size", None)
    if size is not None and size > max_bytes:
        raise ValidationError(SIZE_ERROR, params={"size": settings.MAX_UPLOAD_SIZE_MB})
