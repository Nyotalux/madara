"""Consultations médicales (jalon 4).

Une consultation est l'acte médical : elle prolonge un rendez-vous (facultatif)
ou se crée librement pour un patient. Elle porte le motif, l'examen, le
diagnostic, la conduite à tenir, les constantes et les ordonnances. Les règles de
gestion (démarrage depuis la salle d'attente, clôture) vivent dans
``apps.consultations.services``.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.accounts.models import Membership
from apps.appointments.models import Appointment
from apps.common.models import BaseModel, CommentMixin
from apps.common.numbers import next_sequence
from apps.patients.models import Patient


class VitalSignsMixin(models.Model):
    """Constantes relevées pendant la consultation.

    Toutes les valeurs sont facultatives : un praticien peut enregistrer une
    consultation sans avoir l'appareil sous la main.
    """

    VITAL_FIELDS = (
        "temperature_c",
        "systolic",
        "diastolic",
        "heart_rate",
        "respiratory_rate",
        "oxygen_saturation",
        "blood_glucose",
        "weight_kg",
        "height_cm",
    )

    temperature_c = models.DecimalField(
        _("température (°C)"),
        max_digits=4,
        decimal_places=1,
        null=True,
        blank=True,
        validators=[MinValueValidator(30), MaxValueValidator(45)],
    )
    systolic = models.PositiveSmallIntegerField(
        _("tension systolique (mmHg)"),
        null=True,
        blank=True,
        validators=[MinValueValidator(40), MaxValueValidator(300)],
    )
    diastolic = models.PositiveSmallIntegerField(
        _("tension diastolique (mmHg)"),
        null=True,
        blank=True,
        validators=[MinValueValidator(20), MaxValueValidator(200)],
    )
    heart_rate = models.PositiveSmallIntegerField(
        _("fréquence cardiaque (bpm)"),
        null=True,
        blank=True,
        validators=[MinValueValidator(20), MaxValueValidator(250)],
    )
    respiratory_rate = models.PositiveSmallIntegerField(
        _("fréquence respiratoire (/min)"),
        null=True,
        blank=True,
        validators=[MinValueValidator(5), MaxValueValidator(80)],
    )
    oxygen_saturation = models.PositiveSmallIntegerField(
        _("saturation en oxygène (%)"),
        null=True,
        blank=True,
        validators=[MinValueValidator(50), MaxValueValidator(100)],
    )
    blood_glucose = models.DecimalField(
        _("glycémie (g/L)"),
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(10)],
    )
    weight_kg = models.DecimalField(
        _("poids (kg)"),
        max_digits=5,
        decimal_places=1,
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(400)],
    )
    height_cm = models.PositiveSmallIntegerField(
        _("taille (cm)"),
        null=True,
        blank=True,
        validators=[MinValueValidator(30), MaxValueValidator(250)],
    )

    class Meta:
        abstract = True

    @property
    def has_vital_signs(self) -> bool:
        return any(getattr(self, field) is not None for field in self.VITAL_FIELDS)

    @property
    def blood_pressure_display(self) -> str:
        if self.systolic and self.diastolic:
            return f"{self.systolic}/{self.diastolic} mmHg"
        return ""

    @property
    def bmi(self) -> float | None:
        """Indice de masse corporelle, calculé à partir du poids et de la taille."""
        if self.weight_kg and self.height_cm:
            height_m = float(self.height_cm) / 100
            if height_m:
                return round(float(self.weight_kg) / (height_m**2), 1)
        return None


class Consultation(BaseModel, VitalSignsMixin, CommentMixin):
    """Compte rendu d'une consultation médicale."""

    class Status(models.TextChoices):
        IN_PROGRESS = "IN_PROGRESS", _("En cours")
        COMPLETED = "COMPLETED", _("Terminée")
        CANCELLED = "CANCELLED", _("Annulée")

    class Kind(models.TextChoices):
        FIRST = "FIRST", _("Première visite")
        FOLLOW_UP = "FOLLOW_UP", _("Suivi")
        CONTROL = "CONTROL", _("Contrôle")
        URGENCY = "URGENCY", _("Urgence")
        PROCEDURE = "PROCEDURE", _("Acte technique")

    reference = models.CharField(
        _("référence"),
        max_length=32,
        blank=True,
        editable=False,
        help_text=_("Numéro attribué automatiquement (CONS-AAAA-00001)."),
    )
    patient = models.ForeignKey(
        Patient,
        verbose_name=_("patient"),
        on_delete=models.CASCADE,
        related_name="consultations",
    )
    practitioner = models.ForeignKey(
        Membership,
        verbose_name=_("praticien"),
        on_delete=models.PROTECT,
        related_name="consultations",
        help_text=_("Médecin ou infirmier ayant réalisé la consultation."),
    )
    appointment = models.OneToOneField(
        Appointment,
        verbose_name=_("rendez-vous"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="consultation",
        help_text=_(
            "Rendez-vous d'origine, si la consultation vient de la salle d'attente."
        ),
    )

    status = models.CharField(
        _("statut"), max_length=16, choices=Status.choices, default=Status.IN_PROGRESS
    )
    kind = models.CharField(
        _("type"), max_length=16, choices=Kind.choices, default=Kind.FIRST
    )
    started_at = models.DateTimeField(_("début"), default=timezone.now)
    ended_at = models.DateTimeField(_("fin"), null=True, blank=True)

    # -- Contenu clinique
    chief_complaint = models.CharField(_("motif"), max_length=255, blank=True)
    history = models.TextField(_("antécédents et anamnèse"), blank=True)
    examination = models.TextField(_("examen clinique"), blank=True)
    diagnosis = models.TextField(_("diagnostic"), blank=True)
    treatment = models.TextField(_("conduite à tenir"), blank=True)
    follow_up_date = models.DateField(_("revoir le"), null=True, blank=True)

    class Meta:
        verbose_name = _("consultation")
        verbose_name_plural = _("consultations")
        ordering = ["-started_at"]
        indexes = [
            models.Index(fields=["clinic", "started_at"]),
            models.Index(fields=["patient", "started_at"]),
            models.Index(fields=["practitioner", "started_at"]),
            models.Index(fields=["clinic", "status"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["clinic", "reference"],
                name="uniq_consultation_reference_per_clinic",
            ),
            models.CheckConstraint(
                condition=models.Q(ended_at__isnull=True)
                | models.Q(ended_at__gte=models.F("started_at")),
                name="ck_consultation_end_after_start",
            ),
        ]

    def __str__(self):
        return f"{self.reference or '—'} — {self.patient} ({self.get_status_display()})"

    # -- Persistence ------------------------------------------------------

    def save(self, *args, **kwargs):
        if self.patient_id and self.patient.clinic_id:
            if self.clinic_id and self.clinic_id != self.patient.clinic_id:
                raise ValidationError(_("Le patient appartient à une autre clinique."))
            if not self.clinic_id:
                self.clinic = self.patient.clinic
        if self.status == self.Status.COMPLETED and self.ended_at is None:
            self.ended_at = timezone.now()
        if not self.reference:
            self.reference = next_sequence(self.clinic, "consultation", "CONS", digits=5)
        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        errors = {}
        if (
            self.patient_id
            and self.practitioner_id
            and self.patient.clinic_id != self.practitioner.clinic_id
        ):
            errors["practitioner"] = _(
                "Le praticien n'exerce pas dans la clinique du patient."
            )
        if self.started_at and self.ended_at and self.ended_at < self.started_at:
            errors["ended_at"] = _("La fin doit être postérieure au début.")
        if (
            self.appointment_id
            and self.patient_id
            and self.appointment.patient_id != self.patient_id
        ):
            errors["appointment"] = _("Le rendez-vous concerne un autre patient.")
        if errors:
            raise ValidationError(errors)

    # -- Cycle de vie -----------------------------------------------------

    @property
    def is_completed(self) -> bool:
        return self.status == self.Status.COMPLETED

    @property
    def is_in_progress(self) -> bool:
        return self.status == self.Status.IN_PROGRESS

    @property
    def can_be_completed(self) -> bool:
        return self.status == self.Status.IN_PROGRESS

    def complete(self, *, user=None) -> None:
        """Clôt la consultation et, le cas échéant, le rendez-vous associé."""
        from .services import complete_consultation

        complete_consultation(self, user=user)

    # -- Affichage --------------------------------------------------------

    @property
    def duration_minutes(self) -> int:
        if not (self.started_at and self.ended_at):
            return 0
        return int((self.ended_at - self.started_at).total_seconds() // 60)

    @property
    def display_date(self) -> str:
        return (
            timezone.localtime(self.started_at).strftime("%d/%m/%Y")
            if self.started_at
            else "—"
        )

    @property
    def local_date(self):
        return timezone.localtime(self.started_at).date() if self.started_at else None

    @property
    def practitioner_name(self) -> str:
        return self.practitioner.user.full_name if self.practitioner_id else "—"

    @property
    def status_css(self) -> str:
        return f"badge--{self.status.lower().replace('_', '-')}"

    @property
    def kind_label(self) -> str:
        return self.get_kind_display()

    @property
    def prescription_count(self) -> int:
        return self.prescriptions.count()


class Prescription(BaseModel, CommentMixin):
    """Ordonnance rattachée à une consultation."""

    reference = models.CharField(
        _("référence"),
        max_length=32,
        blank=True,
        editable=False,
        help_text=_("Numéro attribué automatiquement (ORD-AAAA-00001)."),
    )
    consultation = models.ForeignKey(
        Consultation,
        verbose_name=_("consultation"),
        on_delete=models.CASCADE,
        related_name="prescriptions",
    )
    patient = models.ForeignKey(
        Patient,
        verbose_name=_("patient"),
        on_delete=models.CASCADE,
        related_name="prescriptions",
    )
    practitioner = models.ForeignKey(
        Membership,
        verbose_name=_("prescripteur"),
        on_delete=models.PROTECT,
        related_name="prescriptions",
    )
    issued_at = models.DateField(_("établie le"), default=timezone.localdate)

    class Meta:
        verbose_name = _("ordonnance")
        verbose_name_plural = _("ordonnances")
        ordering = ["-issued_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["clinic", "reference"],
                name="uniq_prescription_reference_per_clinic",
            )
        ]

    def __str__(self):
        return f"{self.reference or '—'} ({self.consultation_id})"

    def save(self, *args, **kwargs):
        if self.consultation_id:
            self.patient = self.consultation.patient
            self.clinic = self.consultation.clinic
            if not self.practitioner_id:
                self.practitioner = self.consultation.practitioner
        if not self.reference:
            self.reference = next_sequence(self.clinic, "prescription", "ORD", digits=5)
        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        if (
            self.consultation_id
            and self.patient_id
            and self.consultation.patient_id != self.patient_id
        ):
            raise ValidationError(_("Le patient ne correspond pas à la consultation."))

    @property
    def item_count(self) -> int:
        return self.items.count()

    @property
    def has_items(self) -> bool:
        return self.items.exists()

    @property
    def patient_name(self) -> str:
        return self.patient.display_name if self.patient_id else "—"

    @property
    def practitioner_name(self) -> str:
        return self.practitioner.user.full_name if self.practitioner_id else "—"


class PrescriptionItem(models.Model):
    """Ligne d'ordonnance : médicament, posologie, durée."""

    prescription = models.ForeignKey(
        Prescription,
        verbose_name=_("ordonnance"),
        on_delete=models.CASCADE,
        related_name="items",
    )
    medication = models.CharField(_("médicament"), max_length=160)
    dosage = models.CharField(_("dosage"), max_length=120, blank=True)
    frequency = models.CharField(_("fréquence"), max_length=120, blank=True)
    duration = models.CharField(_("durée"), max_length=80, blank=True)
    instructions = models.CharField(_("consignes"), max_length=255, blank=True)

    class Meta:
        verbose_name = _("ligne d'ordonnance")
        verbose_name_plural = _("lignes d'ordonnance")
        ordering = ["id"]

    def __str__(self):
        return self.medication
