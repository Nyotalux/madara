"""Dossiers patients : identité, contact, couverture sociale et repères médicaux."""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.common.models import BaseModel, CommentMixin, UUIDModel
from apps.common.numbers import (
    age_from_birth_date,
    format_phone_fr,
    full_name,
    initials,
    next_sequence,
    normalize_phone,
)

CIN_RE = r"^[A-Z]{1,2}\d{1,6}$"
CIN_VALIDATOR = RegexValidator(
    regex=CIN_RE,
    message=_("Format de CIN invalide (ex. : AB123456)."),
)


class Patient(BaseModel, UUIDModel, CommentMixin):
    """Dossier patient, unique par clinique."""

    class Gender(models.TextChoices):
        FEMALE = "F", _("Femme")
        MALE = "M", _("Homme")
        OTHER = "O", _("Autre")

    class BloodType(models.TextChoices):
        A_POS = "A+", _("A positif")
        A_NEG = "A-", _("A négatif")
        B_POS = "B+", _("B positif")
        B_NEG = "B-", _("B négatif")
        AB_POS = "AB+", _("AB positif")
        AB_NEG = "AB-", _("AB négatif")
        O_POS = "O+", _("O positif")
        O_NEG = "O-", _("O négatif")
        UNKNOWN = "", _("Inconnu")

    reference = models.CharField(
        _("référence"),
        max_length=32,
        blank=True,
        editable=False,
        help_text=_("Numéro de dossier attribué automatiquement (PT-AAAA-00001)."),
    )
    first_name = models.CharField(_("prénom"), max_length=80)
    last_name = models.CharField(_("nom"), max_length=80)
    birth_date = models.DateField(_("date de naissance"), null=True, blank=True)
    gender = models.CharField(_("sexe"), max_length=1, choices=Gender.choices, blank=True)

    # -- Identité administrative
    cin = models.CharField(
        _("CIN"),
        max_length=12,
        blank=True,
        validators=[CIN_VALIDATOR],
        help_text=_("Carte d'identité nationale."),
    )
    passport = models.CharField(_("passeport"), max_length=20, blank=True)
    social_security = models.CharField(
        _("numéro de sécurité sociale"), max_length=20, blank=True
    )

    # -- Contact
    phone = models.CharField(_("téléphone"), max_length=20, blank=True)
    phone_secondary = models.CharField(_("téléphone 2"), max_length=20, blank=True)
    email = models.EmailField(_("e-mail"), blank=True)
    address = models.TextField(_("adresse"), blank=True)
    city = models.CharField(_("ville"), max_length=100, blank=True)
    emergency_contact_name = models.CharField(
        _("contact d'urgence"), max_length=120, blank=True
    )
    emergency_contact_phone = models.CharField(
        _("téléphone d'urgence"), max_length=20, blank=True
    )

    # -- Couverture sociale
    insurer = models.CharField(_("mutuelle / assureur"), max_length=120, blank=True)
    insurance_number = models.CharField(_("numéro d'adhésion"), max_length=40, blank=True)
    insurance_expiry = models.DateField(_("fin de validité"), null=True, blank=True)
    is_insured = models.BooleanField(_("assuré"), default=False)

    # -- Repères médicaux (le dossier médical complet arrive au jalon 4)
    blood_type = models.CharField(
        _("groupe sanguin"),
        max_length=3,
        choices=BloodType.choices,
        blank=True,
        default="",
    )
    height_cm = models.PositiveSmallIntegerField(
        _("taille (cm)"),
        null=True,
        blank=True,
        validators=[MinValueValidator(30), MaxValueValidator(250)],
    )
    weight_kg = models.DecimalField(
        _("poids (kg)"),
        max_digits=5,
        decimal_places=1,
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(400)],
    )
    allergies = models.TextField(_("allergies connues"), blank=True)
    chronic_conditions = models.TextField(_("maladies chroniques"), blank=True)
    current_medication = models.TextField(_("traitement en cours"), blank=True)
    is_pregnant = models.BooleanField(_("grossesse en cours"), default=False)
    preferred_language = models.CharField(
        _("langue parlée"), max_length=8, blank=True, default="fr"
    )

    class Meta:
        verbose_name = _("patient")
        verbose_name_plural = _("patients")
        ordering = ["last_name", "first_name"]
        indexes = [
            models.Index(fields=["clinic", "last_name", "first_name"]),
            models.Index(fields=["clinic", "reference"]),
            models.Index(fields=["clinic", "phone"]),
            models.Index(fields=["clinic", "birth_date"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["clinic", "reference"], name="uniq_patient_reference_per_clinic"
            )
        ]

    def __str__(self):
        return f"{full_name(self.first_name, self.last_name)} ({self.reference or '—'})"

    # -- Cycle de vie -----------------------------------------------------

    def clean(self):
        """Normalise les identifiants avant validation.

        Appelé par ``full_clean()``, donc par les ``ModelForm`` web. ``save()``
        rappelle cette normalisation afin que l'API soit couverte aussi.
        """
        super().clean()
        self.cin = (self.cin or "").strip().upper()
        self.passport = (self.passport or "").strip().upper()
        self.phone = normalize_phone(self.phone)
        self.phone_secondary = normalize_phone(self.phone_secondary)
        self.emergency_contact_phone = normalize_phone(self.emergency_contact_phone)

    def save(self, *args, **kwargs):
        self.clean()
        if self.birth_date and self.birth_date > timezone.localdate():
            raise ValidationError({"birth_date": _("La date de naissance est future.")})
        if not self.reference:
            self.reference = next_sequence(self.clinic, "patient", "PT", digits=5)
        super().save(*args, **kwargs)

    # -- Affichage --------------------------------------------------------

    @property
    def display_name(self) -> str:
        return full_name(self.first_name, self.last_name)

    @property
    def initials(self) -> str:
        return initials(self.first_name, self.last_name)

    @property
    def age(self) -> int | None:
        return age_from_birth_date(self.birth_date)

    @property
    def age_label(self) -> str:
        value = self.age
        if value is None:
            return _("Age inconnu").title()
        return _("%(age)s ans") % {"age": value}

    @property
    def display_phone(self) -> str:
        return format_phone_fr(self.phone)

    @property
    def display_emergency_phone(self) -> str:
        return format_phone_fr(self.emergency_contact_phone)

    @property
    def insurance_is_expired(self) -> bool:
        return bool(
            self.insurance_expiry and self.insurance_expiry < timezone.localdate()
        )

    @property
    def has_medical_flags(self) -> bool:
        """Signale une allergie ou un traitement en cours (alerte à l'écran)."""
        return bool(self.allergies.strip() or self.current_medication.strip())

    def matches_search(self, needle: str) -> bool:
        """Recherche insensible à la casse sur les champs d'identification."""
        needle = (needle or "").strip().lower()
        if not needle:
            return True
        haystack = " ".join(
            str(value or "")
            for value in (
                self.reference,
                self.first_name,
                self.last_name,
                self.phone,
                self.cin,
                self.email,
                self.city,
            )
        ).lower()
        return needle in haystack
