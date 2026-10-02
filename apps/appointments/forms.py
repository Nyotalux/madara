"""Formulaires de l'agenda (prise de rendez-vous, filtres, statuts)."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from django import forms
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.accounts.models import Membership
from apps.patients.models import Patient

from . import services
from .models import Appointment


class AppointmentForm(forms.ModelForm):
    """Prise et modification d'un rendez-vous.

    Le créneau est choisi dans la liste des disponibilités calculées par
    ``services.available_slots`` : l'utilisateur ne peut pas saisir une heure
    arbitraire.
    """

    patient = forms.ModelChoiceField(
        queryset=Patient.objects.none(),
        label=_("Patient"),
        help_text=_("Recherche par nom, téléphone, CIN ou référence."),
    )
    practitioner = forms.ModelChoiceField(
        queryset=Membership.objects.none(),
        label=_("Praticien"),
        help_text=_("Médecin ou infirmier qui reçoit le patient."),
    )
    day = forms.DateField(
        label=_("Date"),
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        initial=date.today,
    )
    slot = forms.ChoiceField(
        label=_("Créneau disponible"),
        help_text=_("Horaires du praticien, moins ses rendez-vous et indisponibilités."),
    )
    kind = forms.ChoiceField(
        label=_("Type"),
        choices=Appointment.Kind.choices,
        initial=Appointment.Kind.CONSULTATION,
    )
    status = forms.ChoiceField(
        label=_("Statut"),
        choices=Appointment.Status.choices,
        initial=Appointment.Status.CONFIRMED,
    )

    class Meta:
        model = Appointment
        fields = [
            "patient",
            "practitioner",
            "day",
            "slot",
            "kind",
            "status",
            "reason",
            "notes",
        ]
        widgets = {
            "reason": forms.TextInput(
                attrs={"placeholder": _("Fièvre, contrôle post-op…")}
            ),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(
        self, *args, clinic=None, instance=None, lock_practitioner=None, **kwargs
    ):
        self.clinic = clinic or getattr(instance, "clinic", None)
        self.lock_practitioner = lock_practitioner
        super().__init__(*args, instance=instance, **kwargs)
        self._style_fields()

        if self.clinic is not None:
            self.fields["patient"].queryset = Patient.objects.filter(clinic=self.clinic)
            self.fields["practitioner"].queryset = practitioners_of(self.clinic)

        if self.lock_practitioner is not None:
            # Le praticien est verrouillé : le champ n'est pas modifiable.
            self.fields["practitioner"].queryset = Membership.objects.filter(
                pk=self.lock_practitioner.pk
            )
            self.fields["practitioner"].disabled = True
            self.fields["practitioner"].initial = self.lock_practitioner.pk

        if instance is not None and instance.pk:
            self.initial.setdefault("day", instance.local_date)
            self.initial.setdefault("kind", instance.kind)
            self.initial.setdefault("status", instance.status)
            self.initial.setdefault("slot", instance.start_at.strftime("%Y-%m-%dT%H:%M"))

        # Les créneaux disponibles dépendent du praticien et de la date : ils
        # sont recalculés à chaque affichage (et par HTMX).
        self.fields["slot"].choices = self.slot_choices()

    def _style_fields(self):
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, forms.CheckboxInput):
                continue
            widget.attrs["class"] = "form-control"
        # Les créneaux sont optionnels tant que le praticien et la date
        # ne sont pas choisis : ils se rechargent en HTMX.
        self.fields["slot"].required = False
        self.fields["reason"].required = False
        self.fields["notes"].required = False

    # -- Dynamique --------------------------------------------------------

    def available_slots(self):
        """Créneaux proposés pour le praticien et la date choisis."""
        # Pendant la construction du formulaire, ``cleaned_data`` n'existe pas
        # encore : on retombe alors sur les valeurs initiales.
        data = getattr(self, "cleaned_data", {})
        practitioner = self._resolve_practitioner(data.get("practitioner"))
        if practitioner is None:
            practitioner = self._resolve_practitioner(
                self._bound_value("practitioner") or self.initial.get("practitioner")
            )
        day = data.get("day")
        if day is None:
            day = self._bound_value("day") or self.initial.get("day")
        if not practitioner or not day or self.clinic is None:
            return []
        return services.available_slots(
            practitioner,
            day,
            clinic=self.clinic,
            tz=services.clinic_timezone(self.clinic),
            ignore_appointment=self.instance if self.instance.pk else None,
        )

    def _resolve_practitioner(self, value):
        """Accepte un praticien, son identifiant (valeur initiale) ou ``None``."""
        if not value:
            return None
        if isinstance(value, Membership):
            return value
        return self.fields["practitioner"].queryset.filter(pk=value).first()

    def _bound_value(self, name):
        value = self.data.get(name) if self.is_bound else None
        if not value:
            return None
        if name == "practitioner":
            return self.fields["practitioner"].queryset.filter(pk=value).first()
        if name == "day" and isinstance(value, str):
            try:
                return datetime.strptime(value, "%Y-%m-%d").date()
            except ValueError:
                return None
        return value

    def slot_choices(self):
        choices = []
        for start, end in self.available_slots():
            choices.append(
                (
                    start.strftime("%Y-%m-%dT%H:%M"),
                    f"{start:%H:%M} – {end:%H:%M}",
                )
            )
        return choices

    # -- Validation --------------------------------------------------------

    def clean_day(self):
        day = self.cleaned_data["day"]
        editing = bool(self.instance.pk)
        if day < timezone.localdate() and not editing:
            raise ValidationError(_("La date du rendez-vous est déjà passée."))
        return day

    def clean_slot(self):
        value = (self.data.get("slot") or "").strip()
        if not value:
            return ""
        if value not in dict(self.slot_choices()):
            raise ValidationError(
                _("Ce créneau n'est plus disponible : choisissez-en un autre.")
            )
        return value

    def clean(self):
        cleaned = super().clean()
        practitioner = cleaned.get("practitioner")
        day = cleaned.get("day")
        slot = cleaned.get("slot")
        if practitioner is None or day is None or self.clinic is None:
            return cleaned

        start_at = None
        if slot:
            try:
                start_at = datetime.strptime(slot, "%Y-%m-%dT%H:%M")
            except ValueError:
                self.add_error("slot", _("Créneau invalide."))
                return cleaned
        else:
            # Pas de créneau choisi : on propose automatiquement le premier libre.
            slots = self.available_slots()
            if not slots:
                self.add_error(
                    "practitioner",
                    _(
                        "Aucun créneau disponible ce jour-là (hors horaires ou "
                        "indisponibilités). modifier les horaires ou choisir "
                        "une autre date."
                    ),
                )
                return cleaned
            # Heure locale du premier créneau libre : la conversion en datetime
            # conscient est faite juste après, avec le fuseau de la clinique.
            start_at = datetime.combine(day, slots[0][0].time())
            cleaned["slot"] = start_at.strftime("%Y-%m-%dT%H:%M")

        zone = services.clinic_timezone(self.clinic)
        cleaned["start_at"] = (
            timezone.make_aware(start_at, zone)
            if timezone.is_naive(start_at)
            else start_at.astimezone(zone)
        )
        duration = self._duration_minutes(practitioner, self.clinic)
        cleaned["end_at"] = cleaned["start_at"] + timedelta(minutes=duration)

        if self.instance.pk:
            # Déplacement : on ignore l'ancien créneau dans le calcul des conflits.
            found = services.conflicts(
                practitioner=practitioner,
                start_at=cleaned["start_at"],
                end_at=cleaned["end_at"],
                clinic=self.clinic,
                patient=cleaned.get("patient"),
                exclude_pk=self.instance.pk,
            )
            if found["appointments"]:
                self.add_error(
                    "slot", _("Ce créneau vient d'être pris par un autre rendez-vous.")
                )
            elif found["blocked_slots"]:
                self.add_error(
                    "slot",
                    _("Le praticien est indisponible : %s.")
                    % (found["blocked_slots"][0].reason or _("créneau bloqué")),
                )
        return cleaned

    def _duration_minutes(self, practitioner, clinic):
        profile = getattr(practitioner, "practitioner_profile", None)
        if profile is not None and profile.consultation_duration_minutes:
            return profile.consultation_duration_minutes
        return clinic.appointment_slot_minutes or 20

    def save(self, commit=True):
        """Enregistre via ``services`` pour repasser le contrôle des conflits."""
        if not commit:
            return super().save(commit=False)

        if self.instance.pk:
            instance = self.instance
            instance.patient = self.cleaned_data["patient"]
            instance.practitioner = self.cleaned_data["practitioner"]
            instance.start_at = self.cleaned_data["start_at"]
            instance.end_at = self.cleaned_data["end_at"]
            instance.kind = self.cleaned_data["kind"]
            instance.reason = self.cleaned_data.get("reason", "")
            instance.notes = self.cleaned_data.get("notes", "")
            with transaction.atomic():
                # ``full_clean`` recontrôle les chevauchements sur le nouveau
                # créneau, en excluant le rendez-vous en cours d'édition.
                instance.full_clean()
                instance.save()
            self.instance = instance
            return instance

        appointment = services.book_appointment(
            clinic=self.clinic,
            patient=self.cleaned_data["patient"],
            practitioner=self.cleaned_data["practitioner"],
            start_at=self.cleaned_data["start_at"],
            end_at=self.cleaned_data["end_at"],
            kind=self.cleaned_data["kind"],
            status=self.cleaned_data["status"],
            reason=self.cleaned_data.get("reason", ""),
            notes=self.cleaned_data.get("notes", ""),
        )
        self.instance = appointment
        return appointment


class AppointmentFilterForm(forms.Form):
    """Filtres de l'agenda (jour, praticien, statut)."""

    practitioner = forms.ModelChoiceField(
        queryset=Membership.objects.none(),
        label=_("Praticien"),
        required=False,
        widget=forms.Select(attrs={"class": "form-control"}),
    )
    status = forms.ChoiceField(
        label=_("Statut"),
        required=False,
        choices=[("", _("Tous")), *Appointment.Status.choices],
        widget=forms.Select(attrs={"class": "form-control"}),
    )
    kind = forms.ChoiceField(
        label=_("Type"),
        required=False,
        choices=[("", _("Tous")), *Appointment.Kind.choices],
        widget=forms.Select(attrs={"class": "form-control"}),
    )
    q = forms.CharField(
        label=_("Recherche"),
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": _("Nom du patient, téléphone, référence…"),
            }
        ),
    )

    def __init__(self, *args, clinic=None, practitioners=None, **kwargs):
        super().__init__(*args, **kwargs)
        queryset = (
            practitioners
            if practitioners is not None
            else (practitioners_of(clinic) if clinic else Membership.objects.none())
        )
        self.fields["practitioner"].queryset = queryset


class AppointmentStatusForm(forms.Form):
    """Changement de statut ou annulation avec motif."""

    status = forms.ChoiceField(
        label=_("Nouveau statut"),
        choices=Appointment.Status.choices,
        widget=forms.Select(attrs={"class": "form-control"}),
    )
    reason = forms.CharField(
        label=_("Motif"),
        required=False,
        max_length=255,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )

    def __init__(self, *args, appointment=None, **kwargs):
        self.appointment = appointment
        super().__init__(*args, **kwargs)
        if appointment is not None:
            self.fields["status"].choices = [
                (value, label)
                for value, label in Appointment.Status.choices
                if value == appointment.status or appointment.can_transition_to(value)
            ]
            self.initial.setdefault("status", appointment.status)

    def clean_status(self):
        status = self.cleaned_data["status"]
        if self.appointment is None:
            return status
        if status == self.appointment.status:
            raise ValidationError(_("Le rendez-vous est déjà dans ce statut."))
        if not self.appointment.can_transition_to(status):
            labels = dict(Appointment.Status.choices)
            raise ValidationError(
                _("Transition impossible : %(current)s → %(target)s.")
                % {
                    "current": labels.get(
                        self.appointment.status, self.appointment.status
                    ),
                    "target": labels.get(status, status),
                }
            )
        return status


class SlotPickerForm(forms.Form):
    """Formulaire léger pour l'endpoint HTMX « créneaux disponibles »."""

    practitioner = forms.ModelChoiceField(
        queryset=Membership.objects.none(), label=_("Praticien")
    )
    day = forms.DateField(label=_("Date"))

    def __init__(self, *args, clinic=None, **kwargs):
        super().__init__(*args, **kwargs)
        if clinic is not None:
            self.fields["practitioner"].queryset = practitioners_of(clinic)


# ---------------------------------------------------------------------------
# Aides
# ---------------------------------------------------------------------------

BOOKING_ROLES = ("RECEPTION", "DOCTOR", "NURSE", "ADMIN")


def practitioners_of(clinic):
    """Praticiens réservables : médecins et infirmiers actifs de la clinique."""
    return (
        Membership.objects.filter(
            clinic=clinic,
            is_active=True,
            role__in=[Membership.Role.DOCTOR, Membership.Role.NURSE],
        )
        .select_related("user", "practitioner_profile")
        .order_by("user__last_name", "user__first_name")
    )


def slot_time_label(slot_start, slot_end=None) -> str:
    return (
        f"{slot_start:%H:%M}"
        if slot_end is None
        else f"{slot_start:%H:%M} – {slot_end:%H:%M}"
    )


def parse_day(value: str | None, default: date | None = None) -> date:
    """Lit un ``?date=AAAA-MM-JJ`` et retombe sur aujourd'hui."""
    if value:
        try:
            return datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            pass
    return default or timezone.localdate()
