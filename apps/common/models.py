"""Abstractions de modèles communes à toutes les applications.

Deux mécanismes se combinent :

1. ``ClinicScopedModel`` ajoute un champ ``clinic`` (cloisonnement multi-clinique)
   et un manager qui filtre automatiquement sur la clinique courante.
2. ``SoftDeleteModel`` ajoute une suppression logique (``is_active``) : le manager
   ``objects`` masque les archives, ``all_objects`` les conserve.

``BaseModel`` (nom exported dans ``apps.common.mixins``) regroupe l'association
la plus courante : horodatage + auteur + clinique + archivage.
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .context import current_clinic, current_user

# ---------------------------------------------------------------------------
# QuerySets
# ---------------------------------------------------------------------------


class ClinicScopedQuerySet(models.QuerySet):
    """QuerySet conscient du cloisonnement multi-clinique."""

    def for_clinic(self, clinic):
        """Restreint explicitement à une clinique (tâches de fond, admin, scripts)."""
        if clinic is None:
            return self.none()
        return self.filter(clinic=clinic)

    def for_user(self, user):
        """Restreint aux cliniques où l'utilisateur possède un membership actif."""
        if user is None or not user.is_authenticated:
            return self.none()
        if user.is_platform_staff:
            return self
        clinic_ids = user.memberships.filter(is_active=True).values_list(
            "clinic_id", flat=True
        )
        return self.filter(clinic_id__in=list(clinic_ids))

    def exclude_archived(self):
        return self.filter(is_active=True)


class SoftDeleteQuerySet(ClinicScopedQuerySet):
    def delete(self):
        """Suppression logique : on archive au lieu de détruire la ligne."""
        return self.update(is_active=False, deleted_at=timezone.now())


# ---------------------------------------------------------------------------
# Managers
# ---------------------------------------------------------------------------


def _scope_by_current_clinic(queryset):
    """Applique le filtre de clinique courante sauf pour les comptes plateforme."""
    user = current_user.get()
    if user is not None and getattr(user, "is_platform_staff", False):
        return queryset
    clinic = current_clinic.get()
    if clinic is None:
        return queryset
    return queryset.filter(clinic=clinic)


class ClinicScopedManager(models.Manager.from_queryset(ClinicScopedQuerySet)):
    """Filtre automatiquement sur la clinique courante.

    Hors contexte web (tâche Celery, commande de management, script shell),
    aucune clinique n'est courante : aucun filtre n'est appliqué et l'appelant
    doit utiliser ``for_clinic()`` explicitement.
    """

    def get_queryset(self):
        return _scope_by_current_clinic(super().get_queryset())


class UnscopedManager(models.Manager.from_queryset(ClinicScopedQuerySet)):
    """Manager ignorant la clinique courante (admin, maintenance, exports)."""

    def get_queryset(self):
        return ClinicScopedQuerySet(self.model, using=self._db)


class ArchivedHideQuerySetMixin:
    """Manager ``objects`` : masque les lignes archivées."""


class SoftDeleteClinicScopedManager(
    models.Manager.from_queryset(SoftDeleteQuerySet), ArchivedHideQuerySetMixin
):
    def get_queryset(self):
        return _scope_by_current_clinic(super().get_queryset()).filter(is_active=True)


class UnscopedSoftDeleteManager(models.Manager.from_queryset(SoftDeleteQuerySet)):
    """Manager ``all_objects`` : clinics ignorés, archives visibles."""


# ---------------------------------------------------------------------------
# Modèles abstraits
# ---------------------------------------------------------------------------


class TimeStampedModel(models.Model):
    """Dates de création / modification et auteur de la création."""

    created_at = models.DateTimeField(
        _("créé le"), default=timezone.now, editable=False, db_index=True
    )
    updated_at = models.DateTimeField(_("modifié le"), auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("créé par"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        editable=False,
        related_name="%(app_label)s_%(class)s_created",
    )

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if self.created_by_id is None:
            user = current_user.get()
            if user is not None and getattr(user, "is_authenticated", False):
                self.created_by = user
        super().save(*args, **kwargs)


class ClinicScopedModel(TimeStampedModel):
    """Modèle rattaché à une clinique (cloisonnement obligatoire)."""

    clinic = models.ForeignKey(
        "accounts.Clinic",
        verbose_name=_("clinique"),
        on_delete=models.CASCADE,
        related_name="%(app_label)s_%(class)s_rel",
    )

    objects = ClinicScopedManager()
    all_objects = UnscopedManager()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if self.clinic_id is None:
            clinic = current_clinic.get()
            if clinic is not None:
                self.clinic = clinic
        super().save(*args, **kwargs)


class SoftDeleteModel(models.Model):
    """Archivage logique : l'historique médical et comptable est conservé."""

    is_active = models.BooleanField(
        _("actif"),
        default=True,
        db_index=True,
        help_text=_("Décocher pour archiver sans supprimer."),
    )
    deleted_at = models.DateTimeField(_("archivé le"), null=True, blank=True)
    deletion_reason = models.CharField(
        _("motif d'archivage"), max_length=255, blank=True, default=""
    )

    class Meta:
        abstract = True

    def archive(self, reason: str = ""):
        self.is_active = False
        self.deleted_at = timezone.now()
        self.deletion_reason = reason
        self.save(update_fields=["is_active", "deleted_at", "deletion_reason"])
        return self

    def restore(self):
        self.is_active = True
        self.deleted_at = None
        self.deletion_reason = ""
        self.save(update_fields=["is_active", "deleted_at", "deletion_reason"])
        return self


class BaseModel(ClinicScopedModel, SoftDeleteModel):
    """Horodatage + auteur + clinique + archivage logique."""

    objects = SoftDeleteClinicScopedManager()
    all_objects = UnscopedSoftDeleteManager()

    class Meta:
        abstract = True


class UUIDModel(models.Model):
    """Identifiant public non devinable (partages de dossier, liens mobiles)."""

    uuid = models.UUIDField(
        _("identifiant public"), default=uuid.uuid4, editable=False, unique=True
    )

    class Meta:
        abstract = True


class CommentMixin(models.Model):
    """Note libre visible du personnel."""

    notes = models.TextField(_("notes"), blank=True, default="")

    class Meta:
        abstract = True


class SequenceCounter(ClinicScopedModel):
    """Compteur de références séquentielles par clinique et par période.

    Alimente ``apps.common.numbers.next_sequence`` : n° dossier patient,
    n° facture, n° ordonnance, n° bordereau, etc.
    """

    kind = models.CharField(
        _("type de document"),
        max_length=32,
        help_text=_("Ex. : patient, invoice, claim, prescription."),
    )
    period = models.CharField(_("période"), max_length=16, default="")
    prefix = models.CharField(_("préfixe"), max_length=16)
    digits = models.PositiveSmallIntegerField(_("nombre de chiffres"), default=4)
    last_value = models.PositiveIntegerField(_("dernier numéro attribué"), default=0)

    class Meta:
        verbose_name = _("compteur de séquence")
        verbose_name_plural = _("compteurs de séquence")
        constraints = [
            models.UniqueConstraint(
                fields=["clinic", "kind", "period"],
                name="uniq_sequence_per_clinic_kind_period",
            )
        ]

    def __str__(self):
        return f"{self.prefix}-{self.period} ({self.last_value})"
