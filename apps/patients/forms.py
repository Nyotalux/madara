"""Formulaires web des dossiers patients."""

from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy as _

from .models import Patient


class PatientForm(forms.ModelForm):
    """Création et modification d'un dossier patient (rubriques de la fiche)."""

    class Meta:
        model = Patient
        fields = [
            "first_name",
            "last_name",
            "birth_date",
            "gender",
            "cin",
            "passport",
            "social_security",
            "phone",
            "phone_secondary",
            "email",
            "address",
            "city",
            "emergency_contact_name",
            "emergency_contact_phone",
            "is_insured",
            "insurer",
            "insurance_number",
            "insurance_expiry",
            "blood_type",
            "height_cm",
            "weight_kg",
            "allergies",
            "chronic_conditions",
            "current_medication",
            "is_pregnant",
            "preferred_language",
            "notes",
        ]
        widgets = {
            "birth_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "insurance_expiry": forms.DateInput(
                attrs={"type": "date"}, format="%Y-%m-%d"
            ),
            "gender": forms.Select(),
            "blood_type": forms.Select(),
            "preferred_language": forms.Select(
                choices=[("ar", "العربية"), ("fr", "Français"), ("en", "English")]
            ),
            "phone": forms.TextInput(attrs={"placeholder": "06 12 34 56 78"}),
            "cin": forms.TextInput(attrs={"placeholder": "AB123456"}),
            "address": forms.Textarea(attrs={"rows": 2}),
            "allergies": forms.Textarea(attrs={"rows": 2}),
            "chronic_conditions": forms.Textarea(attrs={"rows": 2}),
            "current_medication": forms.Textarea(attrs={"rows": 2}),
            "notes": forms.Textarea(attrs={"rows": 3}),
            "height_cm": forms.NumberInput(attrs={"step": 1, "min": 30, "max": 250}),
            "weight_kg": forms.NumberInput(attrs={"step": "0.1", "min": 1, "max": 400}),
        }

    def __init__(self, *args, clinic=None, **kwargs):
        self.clinic = clinic
        super().__init__(*args, **kwargs)
        self._style_fields()
        if self.instance and self.instance.pk:
            self.fields["notes"].help_text = _(
                "Visible par le personnel autorisé de la clinique."
            )

    def _style_fields(self):
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, (forms.CheckboxInput,)):
                continue
            css = widget.attrs.get("class", "")
            widget.attrs["class"] = f"{css} form-control".strip()

    def clean_cin(self):
        value = (self.cleaned_data.get("cin") or "").strip().upper()
        return value

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("is_insured") and not cleaned.get("insurer"):
            self.add_error("insurer", _("Indiquez l'assureur si le patient est assuré."))
        if cleaned.get("is_pregnant") and cleaned.get("gender") == Patient.Gender.MALE:
            self.add_error("is_pregnant", _("Incohérent avec le sexe indiqué."))
        return cleaned


class PatientFilterForm(forms.Form):
    """Filtres de la liste des patients."""

    q = forms.CharField(
        label=_("Recherche"),
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": _("Nom, téléphone, CIN, référence…"),
            }
        ),
    )
    gender = forms.ChoiceField(
        label=_("Sexe"),
        required=False,
        choices=[("", _("Tous")), *Patient.Gender.choices],
        widget=forms.Select(attrs={"class": "form-control"}),
    )
    city = forms.CharField(
        label=_("Ville"),
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    insured = forms.ChoiceField(
        label=_("Couverture"),
        required=False,
        choices=[("", _("Tous")), ("yes", _("Assurés")), ("no", _("Non assurés"))],
        widget=forms.Select(attrs={"class": "form-control"}),
    )
    status = forms.ChoiceField(
        label=_("Statut"),
        required=False,
        choices=[("", _("Tous")), ("active", _("Actifs")), ("archived", _("Archivés"))],
        widget=forms.Select(attrs={"class": "form-control"}),
    )
    order_by = forms.ChoiceField(
        label=_("Tri"),
        required=False,
        choices=[
            ("last_name", _("Nom")),
            ("-created_at", _("Ajout récent")),
            ("birth_date", _("Date de naissance")),
        ],
        widget=forms.Select(attrs={"class": "form-control"}),
    )

    def cleaned_q(self) -> str:
        return (self.cleaned_data.get("q") or "").strip()


class PatientArchiveForm(forms.Form):
    """Archivage logique d'un dossier patient."""

    reason = forms.CharField(
        label=_("Motif de l'archivage"),
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )

    def clean_reason(self) -> str:
        return (self.cleaned_data.get("reason") or "").strip()[:255]
