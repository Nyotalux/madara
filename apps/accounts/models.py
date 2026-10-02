"""Comptes, cliniques et rôles.

- ``Clinic`` : la tenant. Toute donnée métier y est rattachée.
- ``User`` : identité globale (un même compte peut travailler dans plusieurs
  cliniques via plusieurs ``Membership``).
- ``Membership`` : le rôle du personnel dans une clinique (médecin, infirmier...).
- ``PractitionerProfile`` : informations professionnelles d'un soignant.
- ``OpeningHour`` / ``BlockedSlot`` : planning et indisponibilités.
"""

from __future__ import annotations

from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, RegexValidator
from django.db import models
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.common.models import TimeStampedModel, UUIDModel
from apps.common.numbers import format_phone_fr, normalize_phone, slugify_fr

PHONE_VALIDATOR = RegexValidator(
    r"^\+?[0-9\s\-().]{6,20}$",
    _("Numéro de téléphone invalide : chiffres, espaces, +, - uniquement."),
)

TIME_VALIDATOR = RegexValidator(r"^([01]\d|2[0-3]):[0-5]\d$", _("Heure invalide."))


# ---------------------------------------------------------------------------
# Clinique
# ---------------------------------------------------------------------------


class Clinic(TimeStampedModel):
    """Une clinique : unité de cloisonnement de toutes les données."""

    name = models.CharField(_("nom"), max_length=160)
    legal_name = models.CharField(_("raison sociale"), max_length=200, blank=True)
    slug = models.SlugField(_("identifiant"), max_length=80, unique=True)

    address = models.TextField(_("adresse"), blank=True)
    city = models.CharField(_("ville"), max_length=100, blank=True)
    country = models.CharField(_("pays"), max_length=80, blank=True, default="Maroc")
    phone = models.CharField(
        _("téléphone"), max_length=20, blank=True, validators=[PHONE_VALIDATOR]
    )
    emergency_phone = models.CharField(
        _("urgence"), max_length=20, blank=True, validators=[PHONE_VALIDATOR]
    )
    email = models.EmailField(_("e-mail"), blank=True)
    website = models.URLField(_("site web"), blank=True)

    tax_identifier = models.CharField(_("identifiant fiscal"), max_length=60, blank=True)
    license_number = models.CharField(_("numéro d'agrément"), max_length=60, blank=True)
    logo = models.ImageField(_("logo"), upload_to="clinics/", blank=True, null=True)

    currency = models.CharField(_("devise"), max_length=8, default="MAD")
    timezone = models.CharField(
        _("fuseau horaire"), max_length=64, default="Africa/Casablanca"
    )
    language = models.CharField(_("langue"), max_length=8, default="fr")
    tax_rate = models.DecimalField(
        _("taux de TVA par défaut"),
        max_digits=5,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
    )

    appointment_slot_minutes = models.PositiveSmallIntegerField(
        _("durée d'un créneau (min)"), default=20
    )
    dunning_days = models.PositiveSmallIntegerField(
        _("relance impayé après (jours)"),
        default=15,
        help_text=_("Délai avant relance automatique des factures échues."),
    )

    is_active = models.BooleanField(_("active"), default=True)

    class Meta:
        verbose_name = _("clinique")
        verbose_name_plural = _("cliniques")
        ordering = ["name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify_fr(self.name)
            candidate, index = base, 2
            while Clinic.objects.filter(slug=candidate).exclude(pk=self.pk).exists():
                candidate = f"{base}-{index}"
                index += 1
            self.slug = candidate
        if self.phone:
            self.phone = normalize_phone(self.phone)
        if self.emergency_phone:
            self.emergency_phone = normalize_phone(self.emergency_phone)
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("web:clinic-detail", kwargs={"slug": self.slug})

    @property
    def display_phone(self):
        return format_phone_fr(self.phone)

    @property
    def staff_count(self):
        return self.memberships.filter(is_active=True).count()

    @property
    def doctors(self):
        return self.memberships.filter(
            role=Membership.Role.DOCTOR, is_active=True
        ).select_related("user")


# ---------------------------------------------------------------------------
# Utilisateur
# ---------------------------------------------------------------------------


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, email, password, **extra):
        if not email:
            raise ValueError("Une adresse e-mail est obligatoire.")
        email = self.normalize_email(email).lower()
        user = self.model(email=email, **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra):
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra)

    def create_superuser(self, email, password=None, **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        extra.setdefault("is_platform_staff", True)
        if extra.get("is_superuser") is not True:
            raise ValueError("Un superutilisateur doit avoir is_superuser=True.")
        return self._create_user(email, password, **extra)


class User(AbstractUser):
    """Compte d'accès global, rattaché à une ou plusieurs cliniques.

    Le champ ``username`` d'``AbstractUser`` est supprimé : l'identifiant de
    connexion est l'adresse e-mail.
    """

    username = None

    email = models.EmailField(_("e-mail"), unique=True)
    first_name = models.CharField(_("prénom"), max_length=150, blank=True)
    last_name = models.CharField(_("nom"), max_length=150, blank=True)
    phone = models.CharField(
        _("téléphone"), max_length=20, blank=True, validators=[PHONE_VALIDATOR]
    )
    avatar = models.ImageField(_("photo"), upload_to="avatars/", blank=True, null=True)
    locale = models.CharField(
        _("langue"),
        max_length=8,
        default="fr",
        choices=[("fr", "Français"), ("ar", "العربية"), ("en", "English")],
    )
    is_platform_staff = models.BooleanField(
        _("administrateur plateforme"),
        default=False,
        help_text=_("Accès à toutes les cliniques et à la configuration globale."),
    )
    notification_email = models.EmailField(_("e-mail de notification"), blank=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []

    objects = UserManager()

    class Meta:
        verbose_name = _("utilisateur")
        verbose_name_plural = _("utilisateurs")
        ordering = ["last_name", "first_name"]

    def __str__(self):
        return self.get_full_name() or self.email

    def save(self, *args, **kwargs):
        self.email = self.email.lower().strip()
        if self.phone:
            self.phone = normalize_phone(self.phone)
        super().save(*args, **kwargs)

    # -- Raccourcis ------------------------------------------------------

    def get_full_name(self):
        return f"{self.first_name} {self.last_name}".strip()

    def get_short_name(self):
        return self.first_name or self.email

    @property
    def full_name(self):
        return self.get_full_name() or self.email

    @property
    def memberships(self):
        return self.membership_set.select_related("clinic").order_by("clinic__name")

    @property
    def clinics(self):
        return Clinic.objects.filter(
            membership__user=self, membership__is_active=True
        ).distinct()

    @property
    def active_membership(self):
        """Membership de la clinique courante (défini par le middleware)."""
        from apps.common.context import current_clinic

        clinic = current_clinic.get()
        if clinic is None:
            return None
        return self.memberships.filter(clinic=clinic).first()

    @property
    def primary_membership(self):
        memberships = self.memberships.filter(is_active=True)
        return memberships[0] if memberships else None

    @property
    def role(self):
        membership = self.active_membership or self.primary_membership
        return membership.role if membership else None

    def has_membership(self, clinic) -> bool:
        return self.memberships.filter(clinic=clinic, is_active=True).exists()

    def has_active_membership(self) -> bool:
        if self.is_platform_staff:
            return True
        return self.memberships.filter(is_active=True).exists()

    def role_in(self, clinic) -> str | None:
        membership = self.memberships.filter(clinic=clinic).first()
        return membership.role if membership else None

    def is_doctor(self) -> bool:
        membership = self.active_membership or self.primary_membership
        return bool(membership and membership.role == Membership.Role.DOCTOR)


# ---------------------------------------------------------------------------
# Rôle dans une clinique
# ---------------------------------------------------------------------------


class Membership(UUIDModel, TimeStampedModel):
    """Rattachement d'un utilisateur à une clinique avec un rôle métier."""

    class Role(models.TextChoices):
        ADMIN = "ADMIN", _("Administrateur")
        RECEPTION = "RECEPTION", _("Accueil")
        DOCTOR = "DOCTOR", _("Médecin")
        NURSE = "NURSE", _("Infirmier")
        ACCOUNTANT = "ACCOUNTANT", _("Comptable")

    user = models.ForeignKey(
        User,
        verbose_name=_("utilisateur"),
        on_delete=models.CASCADE,
        related_name="membership_set",
    )
    clinic = models.ForeignKey(
        Clinic,
        verbose_name=_("clinique"),
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    role = models.CharField(_("rôle"), max_length=16, choices=Role.choices)

    employee_number = models.CharField(_("matricule"), max_length=32, blank=True)
    hire_date = models.DateField(_("date d'embauche"), null=True, blank=True)
    contract_end_date = models.DateField(_("fin de contrat"), null=True, blank=True)
    base_salary = models.DecimalField(
        _("salaire de base"),
        max_digits=12,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
    )
    is_active = models.BooleanField(_("actif"), default=True)
    notes = models.TextField(_("notes"), blank=True)

    class Meta:
        verbose_name = _("membre du personnel")
        verbose_name_plural = _("membres du personnel")
        constraints = [
            models.UniqueConstraint(
                fields=["user", "clinic"], name="uniq_membership_user_clinic"
            ),
            models.UniqueConstraint(
                fields=["clinic", "employee_number"],
                condition=~Q(employee_number=""),
                name="uniq_employee_number_per_clinic",
            ),
        ]
        ordering = ["clinic__name", "user__last_name"]

    def __str__(self):
        return f"{self.user} — {self.get_role_display()} @ {self.clinic}"

    def clean(self):
        if self.clinic_id and self.user_id:
            duplicates = Membership.objects.filter(
                user=self.user, clinic=self.clinic
            ).exclude(pk=self.pk)
            if duplicates.exists():
                raise ValidationError(
                    _("Cet utilisateur a déjà un rôle dans cette clinique.")
                )

    @property
    def display_phone(self):
        return format_phone_fr(self.user.phone)

    @property
    def is_contract_expired(self) -> bool:
        return bool(
            self.contract_end_date and self.contract_end_date < timezone.localdate()
        )

    def deactivate(self):
        if self.role == Membership.Role.DOCTOR:
            self.user.memberships.filter(
                clinic=self.clinic, role=Membership.Role.DOCTOR, is_active=True
            ).exclude(pk=self.pk).update(is_active=False)
        self.is_active = False
        self.save(update_fields=["is_active", "updated_at"])


# ---------------------------------------------------------------------------
# Profil professionnel
# ---------------------------------------------------------------------------


class Specialty(models.Model):
    """Spécialité médicale (cardiologie, pédiatrie...)."""

    name = models.CharField(_("spécialité"), max_length=120, unique=True)
    code = models.CharField(_("code"), max_length=20, blank=True)

    class Meta:
        verbose_name = _("spécialité")
        verbose_name_plural = _("spécialités")
        ordering = ["name"]

    def __str__(self):
        return self.name


class PractitionerProfile(models.Model):
    """Informations professionnelles d'un médecin ou infirmier."""

    class Profession(models.TextChoices):
        DOCTOR = "DOCTOR", _("Médecin")
        NURSE = "NURSE", _("Infirmier")

    membership = models.OneToOneField(
        Membership,
        verbose_name=_("membre"),
        on_delete=models.CASCADE,
        related_name="practitioner_profile",
    )
    profession = models.CharField(
        _("profession"), max_length=16, choices=Profession.choices
    )
    specialty = models.ForeignKey(
        Specialty,
        verbose_name=_("spécialité"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="practitioners",
    )
    license_number = models.CharField(_("n° de licence"), max_length=60, blank=True)
    diploma = models.CharField(_("diplôme"), max_length=160, blank=True)
    years_of_experience = models.PositiveSmallIntegerField(
        _("années d'expérience"), default=0
    )
    bio = models.TextField(_("présentation"), blank=True)
    consultation_duration_minutes = models.PositiveSmallIntegerField(
        _("durée de consultation (min)"), default=20
    )
    calendar_color = models.CharField(
        _("couleur agenda"), max_length=7, default="#0d6efd"
    )

    class Meta:
        verbose_name = _("profil professionnel")
        verbose_name_plural = _("profils professionnels")

    def __str__(self):
        return f"{self.membership.user} ({self.get_profession_display()})"

    @property
    def display_name(self) -> str:
        user = self.membership.user
        label = user.full_name
        if self.specialty_id:
            return f"{label} — {self.specialty.name}"
        return label


class OpeningHour(models.Model):
    """Créneau de travail hebdomadaire d'un praticien."""

    membership = models.ForeignKey(
        Membership,
        verbose_name=_("praticien"),
        on_delete=models.CASCADE,
        related_name="opening_hours",
    )
    weekday = models.PositiveSmallIntegerField(
        _("jour"),
        choices=[
            (0, _("Lundi")),
            (1, _("Mardi")),
            (2, _("Mercredi")),
            (3, _("Jeudi")),
            (4, _("Vendredi")),
            (5, _("Samedi")),
            (6, _("Dimanche")),
        ],
    )
    start_time = models.TimeField(_("de"), validators=[TIME_VALIDATOR])
    end_time = models.TimeField(_("à"), validators=[TIME_VALIDATOR])
    is_closed = models.BooleanField(_("fermé"), default=False)

    class Meta:
        verbose_name = _("horaire d'ouverture")
        verbose_name_plural = _("horaires d'ouverture")
        constraints = [
            models.UniqueConstraint(
                fields=["membership", "weekday"], name="uniq_opening_hour_per_day"
            ),
            models.CheckConstraint(
                condition=Q(end_time__gt=models.F("start_time")) | Q(is_closed=True),
                name="ck_opening_hour_end_after_start",
            ),
        ]
        ordering = ["weekday", "start_time"]

    def __str__(self):
        if self.is_closed:
            return f"{self.get_weekday_display()} — fermé"
        return f"{self.get_weekday_display()} — {self.start_time:%H:%M} à {self.end_time:%H:%M}"

    def clean(self):
        if not self.is_closed and self.start_time and self.end_time:
            if self.end_time <= self.start_time:
                raise ValidationError(
                    _("L'heure de fin doit être postérieure à l'heure de début.")
                )


class BlockedSlot(models.Model):
    """Indisponibilité ponctuelle : congés, formation, urgence."""

    membership = models.ForeignKey(
        Membership,
        verbose_name=_("praticien"),
        on_delete=models.CASCADE,
        related_name="blocked_slots",
    )
    start_at = models.DateTimeField(_("début"))
    end_at = models.DateTimeField(_("fin"))
    reason = models.CharField(_("motif"), max_length=160, blank=True)

    class Meta:
        verbose_name = _("indisponibilité")
        verbose_name_plural = _("indisponibilités")
        ordering = ["start_at"]
        constraints = [
            models.CheckConstraint(
                condition=Q(end_at__gt=models.F("start_at")),
                name="ck_blocked_slot_end_after_start",
            )
        ]

    def __str__(self):
        return f"{self.membership.user} indisponible le {self.start_at:%d/%m/%Y %H:%M}"

    def clean(self):
        if self.start_at and self.end_at and self.end_at <= self.start_at:
            raise ValidationError(_("La fin doit être postérieure au début."))

    def overlaps(self, start_at, end_at) -> bool:
        return self.start_at < end_at and start_at < self.end_at
