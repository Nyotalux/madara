"""Règles de gestion des consultations (démarrage, clôture, ordonnances).

Les vues web et l'API passent par ces fonctions : le démarrage depuis la salle
d'attente met à jour le rendez-vous, la clôture termine la consultation et le
rendez-vous associé.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.appointments.models import Appointment

from .models import Consultation, Prescription

#: Rôles habilités à mener une consultation.
CONSULTATION_ROLES = ("DOCTOR", "NURSE", "ADMIN")


@transaction.atomic
def start_consultation(
    *,
    clinic,
    patient,
    practitioner,
    appointment: Appointment | None = None,
    kind: str | None = None,
    chief_complaint: str = "",
    user=None,
) -> Consultation:
    """Ouvre une consultation, en reprenant le rendez-vous si fourni.

    Si le rendez-vous a déjà une consultation active, elle est renvoyée telle
    quelle : un double clic dans la salle d'attente ne crée pas de doublon.
    """
    if patient.clinic_id != clinic.pk:
        raise ValidationError(_("Le patient n'appartient pas à cette clinique."))
    if practitioner.clinic_id != clinic.pk:
        raise ValidationError(_("Le praticien n'exerce pas dans cette clinique."))
    if not practitioner.is_active:
        raise ValidationError(_("Ce praticien n'est plus actif."))

    if appointment is not None:
        if appointment.clinic_id != clinic.pk:
            raise ValidationError(_("Le rendez-vous appartient à une autre clinique."))
        if appointment.patient_id != patient.pk:
            raise ValidationError(_("Le rendez-vous concerne un autre patient."))

        existing = Consultation.all_objects.filter(
            appointment=appointment, is_active=True
        ).first()
        if existing is not None:
            return existing

        # Le patient est reçu : le rendez-vous passe « en consultation ».
        if appointment.status != Appointment.Status.IN_PROGRESS and (
            appointment.can_transition_to(Appointment.Status.IN_PROGRESS)
        ):
            appointment.transition_to(Appointment.Status.IN_PROGRESS, user=user)
        if kind is None:
            kind = _kind_from_appointment(appointment)

    consultation = Consultation(
        clinic=clinic,
        patient=patient,
        practitioner=practitioner,
        appointment=appointment,
        kind=kind or Consultation.Kind.FIRST,
        chief_complaint=chief_complaint,
        created_by=user if getattr(user, "is_authenticated", False) else None,
    )
    consultation.full_clean()
    consultation.save()
    return consultation


@transaction.atomic
def complete_consultation(consultation: Consultation, *, user=None) -> Consultation:
    """Clôt la consultation et termine le rendez-vous associé."""
    if consultation.status == Consultation.Status.COMPLETED:
        return consultation

    consultation.status = Consultation.Status.COMPLETED
    if consultation.ended_at is None:
        consultation.ended_at = timezone.now()
    consultation.save(update_fields=["status", "ended_at", "updated_at"])

    appointment = consultation.appointment
    if appointment is not None and appointment.status not in {
        Appointment.Status.DONE,
        Appointment.Status.CANCELLED,
        Appointment.Status.NO_SHOW,
    }:
        if appointment.status != Appointment.Status.IN_PROGRESS and (
            appointment.can_transition_to(Appointment.Status.IN_PROGRESS)
        ):
            appointment.transition_to(Appointment.Status.IN_PROGRESS, user=user)
        if appointment.can_transition_to(Appointment.Status.DONE):
            appointment.transition_to(Appointment.Status.DONE, user=user)

    return consultation


@transaction.atomic
def cancel_consultation(
    consultation: Consultation, *, reason: str = "", user=None
) -> Consultation:
    """Annule une consultation sans supprimer la trace (statut CANCELLED)."""
    if consultation.status == Consultation.Status.COMPLETED:
        raise ValidationError(_("Une consultation terminée ne peut pas être annulée."))
    if consultation.status == Consultation.Status.CANCELLED:
        return consultation

    consultation.status = Consultation.Status.CANCELLED
    if consultation.ended_at is None:
        consultation.ended_at = timezone.now()
    if reason:
        consultation.notes = (consultation.notes + "\n" + reason).strip()
    consultation.save(update_fields=["status", "ended_at", "notes", "updated_at"])
    return consultation


@transaction.atomic
def add_prescription(
    consultation: Consultation,
    *,
    issued_at=None,
    notes: str = "",
    items: list[dict] | None = None,
    user=None,
) -> Prescription:
    """Crée une ordonnance et ses lignes pour une consultation."""
    if consultation.status == Consultation.Status.CANCELLED:
        raise ValidationError(_("Impossible de prescrire sur une consultation annulée."))

    prescription = Prescription(
        clinic=consultation.clinic,
        consultation=consultation,
        patient=consultation.patient,
        practitioner=consultation.practitioner,
        notes=notes,
        created_by=user if getattr(user, "is_authenticated", False) else None,
    )
    if issued_at:
        prescription.issued_at = issued_at
    prescription.full_clean()
    prescription.save()

    for row in items or []:
        medication = (row.get("medication") or "").strip()
        if not medication:
            continue
        prescription.items.create(
            medication=medication,
            dosage=(row.get("dosage") or "").strip(),
            frequency=(row.get("frequency") or "").strip(),
            duration=(row.get("duration") or "").strip(),
            instructions=(row.get("instructions") or "").strip(),
        )

    return prescription


def _kind_from_appointment(appointment: Appointment) -> str:
    """Reporte le type du rendez-vous sur la consultation quand c'est possible."""
    mapping = {
        Appointment.Kind.CONSULTATION: Consultation.Kind.FIRST,
        Appointment.Kind.CONTROL: Consultation.Kind.CONTROL,
        Appointment.Kind.URGENCY: Consultation.Kind.URGENCY,
        Appointment.Kind.PROCEDURE: Consultation.Kind.PROCEDURE,
    }
    return mapping.get(appointment.kind, Consultation.Kind.FOLLOW_UP)
