"""Formulaires web du dossier médical (antécédents, documents, partages)."""

from __future__ import annotations

from django import forms
from django.conf import settings
from django.utils.translation import gettext_lazy as _

from .models import MedicalDocument, MedicalRecord


class MedicalRecordForm(forms.ModelForm):
    """Antécédents structurés du dossier médical."""

    class Meta:
        model = MedicalRecord
        fields = [
            "family_history",
            "medical_history",
            "surgical_history",
            "gynecological_history",
            "vaccinations",
            "lifestyle",
            "disabilities",
            "notes",
        ]
        widgets = {
            "family_history": forms.Textarea(attrs={"rows": 2}),
            "medical_history": forms.Textarea(attrs={"rows": 2}),
            "surgical_history": forms.Textarea(attrs={"rows": 2}),
            "gynecological_history": forms.Textarea(attrs={"rows": 2}),
            "vaccinations": forms.Textarea(attrs={"rows": 2}),
            "lifestyle": forms.Textarea(attrs={"rows": 2}),
            "disabilities": forms.Textarea(attrs={"rows": 2}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            css = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = f"{css} form-control".strip()


class MedicalDocumentForm(forms.ModelForm):
    """Dépôt d'une pièce dans le dossier médical."""

    class Meta:
        model = MedicalDocument
        fields = ["category", "title", "document_date", "file", "description"]
        widgets = {
            "document_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "description": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            css = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = f"{css} form-control".strip()

    def clean_document_date(self):
        from django.utils import timezone

        value = self.cleaned_data.get("document_date")
        if value and value > timezone.localdate():
            raise forms.ValidationError(_("La date du document est future."))
        return value


class MedicalRecordShareForm(forms.Form):
    """Création d'un lien de partage temporaire."""

    days = forms.IntegerField(
        label=_("Durée (jours)"),
        min_value=1,
        max_value=getattr(settings, "RECORD_SHARE_MAX_DAYS", 30),
        initial=7,
        widget=forms.NumberInput(attrs={"class": "form-control", "min": 1}),
    )
    shared_with = forms.CharField(
        label=_("Destinataire"),
        required=False,
        max_length=160,
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": _("Dr Ahmed, confrère…")}
        ),
    )
    note = forms.CharField(
        label=_("Note"),
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 2}),
    )

    def clean_days(self) -> int:
        days = self.cleaned_data["days"]
        maximum = getattr(settings, "RECORD_SHARE_MAX_DAYS", 30)
        if days > maximum:
            raise forms.ValidationError(
                _("Un partage ne peut pas dépasser %(days)s jours.") % {"days": maximum}
            )
        return days
