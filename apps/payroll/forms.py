"""Formulaires web de la paie (jalon 6)."""

from __future__ import annotations

import re
from datetime import date

from django import forms
from django.utils.translation import gettext_lazy as _

from .models import Bonus, Payslip

_PERIOD_RE = re.compile(r"\d{4}-\d{2}")


class PayrollFilterForm(forms.Form):
    """Filtres de la liste des bulletins."""

    period = forms.RegexField(
        label=_("Période"),
        regex=r"\d{4}-\d{2}",
        max_length=7,
        required=False,
        widget=forms.TextInput(
            attrs={"placeholder": _("AAAA-MM"), "type": "text", "pattern": r"\d{4}-\d{2}"}
        ),
        error_messages={"invalid": _("La période suit le format AAAA-MM.")},
    )
    status = forms.ChoiceField(
        label=_("État"),
        required=False,
        choices=[("", _("Tous"))]
        + [(value, label) for value, label in Payslip.Status.choices],
    )
    q = forms.CharField(
        label=_("Recherche"),
        required=False,
        widget=forms.TextInput(attrs={"placeholder": _("Nom du membre du personnel…")}),
    )

    def clean_period(self):
        return (self.cleaned_data.get("period") or "").strip()


class PeriodChoiceForm(forms.Form):
    """Sélection d'une période de paie (AAAA-MM)."""

    period = forms.RegexField(
        label=_("Période"),
        regex=r"\d{4}-\d{2}",
        max_length=7,
        initial=lambda: date.today().strftime("%Y-%m"),
        widget=forms.TextInput(attrs={"placeholder": _("AAAA-MM")}),
        error_messages={"invalid": _("La période suit le format AAAA-MM.")},
    )


class PayslipForm(forms.ModelForm):
    """Ajustement d'un bulletin brouillon."""

    class Meta:
        model = Payslip
        fields = ["base_amount", "bonus_amount", "deduction_amount", "notes"]
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 2}),
        }

    def clean(self):
        cleaned = super().clean()
        for field in ("base_amount", "bonus_amount", "deduction_amount"):
            value = cleaned.get(field)
            if value is not None and value < 0:
                self.add_error(field, _("Le montant ne peut pas être négatif."))
        return cleaned


class BonusForm(forms.ModelForm):
    """Prime ou retenue ponctuelle d'un membre du personnel."""

    class Meta:
        model = Bonus
        fields = ["membership", "period", "kind", "label", "amount"]
        widgets = {
            "label": forms.TextInput(
                attrs={"placeholder": _("Heures supplémentaires, prime repas, avance…")}
            ),
        }

    def __init__(self, *args, clinic=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.clinic = clinic
        if clinic is not None:
            self.fields["membership"].queryset = clinic.memberships.filter(
                is_active=True
            ).select_related("user")
        if not self.is_bound:
            self.fields["period"].initial = date.today().strftime("%Y-%m")

    def clean_period(self):
        period = (self.cleaned_data.get("period") or "").strip()
        if not _PERIOD_RE.fullmatch(period):
            raise forms.ValidationError(_("La période suit le format AAAA-MM."))
        return period
