"""Agenda et rendez-vous (jalon 3).

Un rendez-vous relie un patient à un praticien sur un créneau horodaté. Les
règles de gestion (créneaux libres, conflits, transitions de statut) vivent dans
``apps.appointments.services`` pour rester testables et réutilisables par l'API.
"""

from __future__ import annotations

from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.accounts.models import BlockedSlot, Membership
from apps.common.models import BaseModel, CommentMixin
from apps.common.numbers import format_time_fr, next_sequence
from apps.patients.models import Patient


class Appointment(BaseModel, CommentMixin):
    """Rendez-vous d'un patient avec un praticien de la clinique."""

    class Status(models.TextChoices):
        PENDING = "PENDING", _("À confirmer")
        CONFIRMED = "CONFIRMED", _("Confirmé")
        ARRIVED = "ARRIVED", _("Arrivé")
        IN_PROGRESS = "IN_PROGRESS", _("En consultation")
        DONE = "DONE", _("Terminé")
        CANCELLED = "CANCELLED", _("Annulé")
        NO_SHOW = "NO_SHOW", _("Absent")

    class Kind(models.TextChoices):
        CONSULTATION = "CONSULTATION", _("Consultation")
        CONTROL = "CONTROL", _("Contrôle")
        URGENCY = "URGENCY", _("Urgence")
        VACCINATION = "VACCINATION", _("Vaccination")
        PROCEDURE = "PROCEDURE", _("Acte technique")
        OTHER = "OTHER", _("Autre")

    #: Statuts qui immobilisent un créneau (les rendez-vous annulés n'en
    #: immobilisent pas et ne doivent pas déclencher de conflit).
    BLOCKING_STATUSES = (
        Status.PENDING,
        Status.CONFIRMED,
        Status.ARRIVED,
        Status.IN_PROGRESS,
        Status.DONE,
    )

    #: Statuts terminaux : on ne peut plus changer un rendez-vous terminé.
    TERMINAL_STATUSES = (Status.DONE,)

    #: Transitions autorisées entre statuts (reprise possible d'un rendez-vous
    #: raté, jamais d'une consultation terminée).
    ALLOWED_TRANSITIONS = {
        Status.PENDING: {
            Status.CONFIRMED,
            Status.ARRIVED,
            Status.IN_PROGRESS,
            Status.CANCELLED,
            Status.NO_SHOW,
        },
        Status.CONFIRMED: {
            Status.ARRIVED,
            Status.IN_PROGRESS,
            Status.CANCELLED,
            Status.NO_SHOW,
        },
        Status.ARRIVED: {
            Status.IN_PROGRESS,
            Status.DONE,
            Status.CANCELLED,
            Status.NO_SHOW,
        },
        Status.IN_PROGRESS: {Status.DONE, Status.CANCELLED},
        Status.DONE: set(),
        Status.CANCELLED: {Status.PENDING, Status.CONFIRMED, Status.ARRIVED},
        Status.NO_SHOW: {Status.PENDING, Status.CONFIRMED, Status.ARRIVED},
    }

    reference = models.CharField(
        _("référence"),
        max_length=32,
        blank=True,
        editable=False,
        help_text=_("Numéro de rendez-vous attribué automatiquement (RDV-AAAA-00001)."),
    )
    patient = models.ForeignKey(
        Patient,
        verbose_name=_("patient"),
        on_delete=models.CASCADE,
        related_name="appointments",
    )
    practitioner = models.ForeignKey(
        Membership,
        verbose_name=_("praticien"),
        on_delete=models.PROTECT,
        related_name="appointments",
        help_text=_("Médecin ou infirmier qui reçoit le patient."),
    )
    start_at = models.DateTimeField(_("début"))
    end_at = models.DateTimeField(_("fin"))
    status = models.CharField(
        _("statut"), max_length=16, choices=Status.choices, default=Status.PENDING
    )
    kind = models.CharField(
        _("type"), max_length=16, choices=Kind.choices, default=Kind.CONSULTATION
    )
    reason = models.CharField(_("motif"), max_length=160, blank=True)
    cancellation_reason = models.CharField(
        _("motif d'annulation"), max_length=255, blank=True
    )
    cancelled_at = models.DateTimeField(_("annulé le"), null=True, blank=True)
    reminder_sent_at = models.DateTimeField(
        _("rappel envoyé le"),
        null=True,
        blank=True,
        help_text=_("Renseigné par le jalon 5 (notifications)."),
    )

    class Meta:
        verbose_name = _("rendez-vous")
        verbose_name_plural = _("rendez-vous")
        ordering = ["start_at"]
        indexes = [
            models.Index(fields=["clinic", "start_at"]),
            models.Index(fields=["practitioner", "start_at"]),
            models.Index(fields=["patient", "start_at"]),
            models.Index(fields=["clinic", "status"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["clinic", "reference"],
                name="uniq_appointment_reference_per_clinic",
            ),
            models.CheckConstraint(
                condition=models.Q(end_at__gt=models.F("start_at")),
                name="ck_appointment_end_after_start",
            ),
        ]

    def __str__(self):
        return f"{self.reference or '—'} — {self.patient} @ {self.display_time}"

    # -- Persistence ------------------------------------------------------

    def save(self, *args, **kwargs):
        if self.patient_id and self.patient.clinic_id:
            # Le rendez-vous appartient toujours à la clinique du patient.
            if self.clinic_id and self.clinic_id != self.patient.clinic_id:
                raise ValidationError(
                    _("Le patient appartient à une autre clinique."),
                )
            if not self.clinic_id:
                self.clinic = self.patient.clinic
        if self.start_at and not self.end_at:
            self.end_at = self.start_at + timedelta(
                minutes=self.default_duration_minutes()
            )
        if not self.reference:
            self.reference = next_sequence(self.clinic, "appointment", "RDV", digits=5)
        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        errors = {}

        if self.start_at and self.end_at and self.end_at <= self.start_at:
            errors["end_at"] = _("La fin doit être postérieure au début.")

        if (
            self.patient_id
            and self.practitioner_id
            and self.patient.clinic_id != self.practitioner.clinic_id
        ):
            errors["practitioner"] = _(
                "Le praticien n'exerce pas dans la clinique du patient."
            )

        if self.start_at and self.end_at and self.end_at > self.start_at:
            errors.update(self._conflict_errors())

        if errors:
            raise ValidationError(errors)

    def _conflict_errors(self) -> dict:
        """Contrôle les chevauchements : praticien, patient, indisponibilité."""
        from .services import conflicts

        found = conflicts(
            practitioner=self.practitioner,
            start_at=self.start_at,
            end_at=self.end_at,
            exclude_pk=self.pk,
            clinic=self.clinic or (self.patient.clinic if self.patient_id else None),
            patient=self.patient if self.patient_id else None,
        )
        errors: dict = {}
        if found["appointments"]:
            other = found["appointments"][0]
            errors["start_at"] = _(
                "Le praticien a déjà un rendez-vous sur ce créneau (%(reference)s).",
            ) % {"reference": other.reference or other.pk}
        if found["blocked_slots"]:
            errors["start_at"] = _(
                "Le praticien est indisponible sur ce créneau : %(reason)s.",
            ) % {"reason": found["blocked_slots"][0].reason or _("indisponibilité")}
        if found["patient"]:
            errors["patient"] = _(
                "Le patient a déjà un rendez-vous à cette heure (%(reference)s).",
            ) % {"reference": found["patient"].reference or found["patient"].pk}
        return errors

    # -- Statuts ----------------------------------------------------------

    def default_duration_minutes(self) -> int:
        """Durée par défaut : profil du praticien, puis créneau de la clinique."""
        profile = getattr(self.practitioner, "practitioner_profile", None)
        if profile is not None and profile.consultation_duration_minutes:
            return profile.consultation_duration_minutes
        if self.clinic_id and self.clinic.appointment_slot_minutes:
            return self.clinic.appointment_slot_minutes
        return 20

    def can_transition_to(self, status: str) -> bool:
        return status in self.ALLOWED_TRANSITIONS.get(self.status, set())

    def transition_to(self, status: str, *, reason: str = "", user=None) -> None:
        """Change le statut en respectant les transitions autorisées.

        Une annulation est journalisée (auteur, motif, horodatage) afin de
        garder une trace même si le rendez-vous est ensuite repris.
        """
        if status not in self.Status.values:
            raise ValidationError(_("Statut de rendez-vous inconnu."))
        if status == self.status:
            return
        if not self.can_transition_to(status):
            labels = dict(self.Status.choices)
            raise ValidationError(
                _("Transition impossible : %(current)s → %(target)s."),
                code="invalid_transition",
                params={
                    "current": labels.get(self.status, self.status),
                    "target": labels.get(status, status),
                },
            )

        self.status = status
        fields = ["status", "updated_at"]
        if status == self.Status.CANCELLED:
            self.cancelled_at = timezone.now()
            self.cancellation_reason = (reason or "").strip()[:255]
            fields += ["cancelled_at", "cancellation_reason"]
        elif status in {self.Status.CONFIRMED, self.Status.ARRIVED}:
            # Une reprise annule l'historique d'annulation.
            self.cancelled_at = None
            self.cancellation_reason = ""
            fields += ["cancelled_at", "cancellation_reason"]
        self.save(update_fields=fields)

        if status == self.Status.CANCELLED:
            AppointmentCancellation.objects.create(
                appointment=self,
                reason=self.cancellation_reason,
                cancelled_by=user if getattr(user, "is_authenticated", False) else None,
            )

    def cancel(self, reason: str = "", user=None) -> None:
        self.transition_to(self.Status.CANCELLED, reason=reason, user=user)

    # -- Affichage --------------------------------------------------------

    @property
    def duration_minutes(self) -> int:
        if not (self.start_at and self.end_at):
            return 0
        return int((self.end_at - self.start_at).total_seconds() // 60)

    @property
    def display_time(self) -> str:
        if not (self.start_at and self.end_at):
            return "—"
        return f"{format_time_fr(self.start_at)} – {format_time_fr(self.end_at)}"

    @property
    def display_date(self) -> str:
        return (
            timezone.localtime(self.start_at).strftime("%d/%m/%Y")
            if self.start_at
            else "—"
        )

    @property
    def local_date(self):
        return timezone.localtime(self.start_at).date() if self.start_at else None

    @property
    def practitioner_name(self) -> str:
        return self.practitioner.user.full_name if self.practitioner_id else "—"

    @property
    def is_cancelled(self) -> bool:
        return self.status == self.Status.CANCELLED

    @property
    def blocks_slot(self) -> bool:
        return self.is_active and self.status in self.BLOCKING_STATUSES

    @property
    def is_past(self) -> bool:
        return bool(self.end_at and self.end_at < timezone.now())

    @property
    def is_today(self) -> bool:
        return self.local_date == timezone.localdate()

    @property
    def can_be_cancelled(self) -> bool:
        return self.can_transition_to(self.Status.CANCELLED)

    @property
    def status_css(self) -> str:
        return f"badge--{self.status.lower().replace('_', '-')}"

    @property
    def kind_label(self) -> str:
        return self.get_kind_display()


class AppointmentCancellation(models.Model):
    """Journal des annulations : motif, auteur, horodatage.

    Le rendez-vous conserve son statut ; ce journal répond à la question
    « qui a annulé, quand et pourquoi ? » même après une reprise.
    """

    appointment = models.ForeignKey(
        Appointment,
        verbose_name=_("rendez-vous"),
        on_delete=models.CASCADE,
        related_name="cancellations",
    )
    reason = models.CharField(_("motif"), max_length=255, blank=True)
    cancelled_by = models.ForeignKey(
        "accounts.User",
        verbose_name=_("annulé par"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="appointment_cancellations",
    )
    created_at = models.DateTimeField(_("le"), auto_now_add=True)

    class Meta:
        verbose_name = _("annulation")
        verbose_name_plural = _("annulations")
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.appointment_id} annulé ({self.reason or '—'})"


def blocked_slot_conflicts(membership: Membership, start_at, end_at):
    """Indisponibilités ponctuelles chevauchant un créneau (utilitaire tests)."""
    return list(
        BlockedSlot.objects.filter(
            membership=membership, start_at__lt=end_at, end_at__gt=start_at
        )
    )
