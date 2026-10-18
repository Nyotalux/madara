"""Formulaires web des consultations et ordonnances (jalon 4)."""

from __future__ import annotations

from django import forms
from django.forms import inlineformset_factory
from django.utils.translation import gettext_lazy as _

from apps.accounts.models import Membership
from apps.appointments.forms import practitioners_of
from apps.patients.models import Patient

from .models import Consultation, Prescription, PrescriptionItem, VitalSignsMixin

VITAL_WIDGETS = {
    "temperature_c": forms.NumberInput(attrs={"step": "0.1", "min": 30, "max": 45}),
    "systolic": forms.NumberInput(attrs={"min": 40, "max": 300}),
    "diastolic": forms.NumberInput(attrs={"min": 20, "max": 200}),
    "heart_rate": forms.NumberInput(attrs={"min": 20, "max": 250}),
    "respiratory_rate": forms.NumberInput(attrs={"min": 5, "max": 80}),
    "oxygen_saturation": forms.NumberInput(attrs={"min": 50, "max": 100}),
    "blood_glucose": forms.NumberInput(attrs={"step": "0.01", "min": 0, "max": 10}),
    "weight_kg": forms.NumberInput(attrs={"step": "0.1", "min": 1, "max": 400}),
    "height_cm": forms.NumberInput(attrs={"min": 30, "max": 250}),
}


class ConsultationStartForm(forms.Form):
    """Ouverture d'une consultation, depuis la salle d'attente ou librement.

    Quand la consultation prolonge un rendez-vous, le patient et le praticien
    sont figés : ils proviennent du rendez-vous.
    """

    patient = forms.ModelChoiceField(
        queryset=Patient.objects.none(),
        label=_("Patient"),
        help_text=_("Recherche par nom, téléphone, CIN ou référence."),
    )
    practitioner = forms.ModelChoiceField(
        queryset=Membership.objects.none(),
        label=_("Praticien"),
        help_text=_("Médecin ou infirmier qui réalise la consultation."),
    )
    kind = forms.ChoiceField(
        label=_("Type"),
        choices=Consultation.Kind.choices,
        initial=Consultation.Kind.FIRST,
    )
    chief_complaint = forms.CharField(
        label=_("Motif"),
        required=False,
        max_length=255,
        widget=forms.TextInput(attrs={"placeholder": _("Fièvre, douleur, contrôle…")}),
    )

    def __init__(
        self,
        *args,
        clinic=None,
        appointment=None,
        lock_practitioner=None,
        **kwargs,
    ):
        self.clinic = clinic
        self.appointment = appointment
        super().__init__(*args, **kwargs)
        self._style_fields()

        if clinic is not None:
            self.fields["patient"].queryset = Patient.objects.filter(clinic=clinic)
            self.fields["practitioner"].queryset = practitioners_of(clinic)

        if appointment is not None:
            from .services import _kind_from_appointment

            self.fields["patient"].queryset = Patient.objects.filter(
                pk=appointment.patient_id
            )
            self.fields["patient"].initial = appointment.patient_id
            self.fields["patient"].disabled = True
            self.fields["practitioner"].queryset = Membership.objects.filter(
                pk=appointment.practitioner_id
            )
            self.fields["practitioner"].initial = appointment.practitioner_id
            self.fields["practitioner"].disabled = True
            self.fields["kind"].initial = _kind_from_appointment(appointment)
            if appointment.reason:
                self.fields["chief_complaint"].initial = appointment.reason

        if lock_practitioner is not None:
            self.fields["practitioner"].queryset = Membership.objects.filter(
                pk=lock_practitioner.pk
            )
            self.fields["practitioner"].disabled = True
            self.fields["practitioner"].initial = lock_practitioner.pk

    def _style_fields(self):
        for field in self.fields.values():
            if isinstance(field.widget, forms.CheckboxInput):
                continue
            field.widget.attrs["class"] = "form-control"
        self.fields["chief_complaint"].required = False


class ConsultationForm(forms.ModelForm):
    """Compte rendu : constante, examen, diagnostic et conduite à tenir."""

    class Meta:
        model = Consultation
        fields = [
            "kind",
            "chief_complaint",
            "history",
            "examination",
            "diagnosis",
            "treatment",
            "follow_up_date",
            *VitalSignsMixin.VITAL_FIELDS,
            "notes",
        ]
        widgets = {
            "follow_up_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "kind": forms.Select(),
            "chief_complaint": forms.TextInput(
                attrs={"placeholder": _("Fièvre, douleur, contrôle…")}
            ),
            "history": forms.Textarea(attrs={"rows": 3}),
            "examination": forms.Textarea(attrs={"rows": 3}),
            "diagnosis": forms.Textarea(attrs={"rows": 3}),
            "treatment": forms.Textarea(attrs={"rows": 3}),
            "notes": forms.Textarea(attrs={"rows": 2}),
            **VITAL_WIDGETS,
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style_fields()

    def _style_fields(self):
        for field in self.fields.values():
            if isinstance(field.widget, forms.CheckboxInput):
                continue
            css = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = f"{css} form-control".strip()


class ConsultationFilterForm(forms.Form):
    """Filtres de la liste des consultations."""

    q = forms.CharField(
        label=_("Recherche"),
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": _("Patient, motif, diagnostic, référence…"),
            }
        ),
    )
    practitioner = forms.ModelChoiceField(
        queryset=Membership.objects.none(),
        label=_("Praticien"),
        required=False,
        widget=forms.Select(attrs={"class": "form-control"}),
    )
    status = forms.ChoiceField(
        label=_("Statut"),
        required=False,
        choices=[("", _("Tous")), *Consultation.Status.choices],
        widget=forms.Select(attrs={"class": "form-control"}),
    )
    kind = forms.ChoiceField(
        label=_("Type"),
        required=False,
        choices=[("", _("Tous")), *Consultation.Kind.choices],
        widget=forms.Select(attrs={"class": "form-control"}),
    )
    du = forms.DateField(
        label=_("Du"),
        required=False,
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}),
    )
    au = forms.DateField(
        label=_("Au"),
        required=False,
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}),
    )

    def __init__(self, *args, clinic=None, practitioners=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["practitioner"].queryset = (
            practitioners
            if practitioners is not None
            else (practitioners_of(clinic) if clinic else Membership.objects.none())
        )


class ConsultationCancelForm(forms.Form):
    """Annulation d'une consultation avec motif."""

    reason = forms.CharField(
        label=_("Motif de l'annulation"),
        required=False,
        max_length=255,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )

    def clean_reason(self) -> str:
        return (self.cleaned_data.get("reason") or "").strip()[:255]


class PrescriptionForm(forms.ModelForm):
    """En-tête d'ordonnance (date, note)."""

    class Meta:
        model = Prescription
        fields = ["issued_at", "notes"]
        widgets = {
            "issued_at": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            css = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = f"{css} form-control".strip()


class PrescriptionItemForm(forms.ModelForm):
    """Ligne d'ordonnance (médicament, posologie, durée)."""

    class Meta:
        model = PrescriptionItem
        fields = ["medication", "dosage", "frequency", "duration", "instructions"]
        widgets = {
            "medication": forms.TextInput(attrs={"placeholder": _("Paracétamol 1 g")}),
            "dosage": forms.TextInput(attrs={"placeholder": _("1 comprimé")}),
            "frequency": forms.TextInput(attrs={"placeholder": _("3 fois par jour")}),
            "duration": forms.TextInput(attrs={"placeholder": _("5 jours")}),
            "instructions": forms.TextInput(attrs={"placeholder": _("Après les repas")}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            css = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = f"{css} form-control".strip()


PrescriptionItemFormSet = inlineformset_factory(
    Prescription,
    PrescriptionItem,
    form=PrescriptionItemForm,
    extra=1,
    can_delete=True,
)
