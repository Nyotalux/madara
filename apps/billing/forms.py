"""Formulaires web de la facturation (jalon 6)."""

from __future__ import annotations

from decimal import Decimal

from django import forms
from django.forms import formset_factory, modelformset_factory
from django.utils.translation import gettext_lazy as _

from apps.consultations.models import Consultation
from apps.patients.models import Patient

from .models import Invoice, InvoiceLine, Payment


class InvoiceForm(forms.ModelForm):
    """En-tête de facture : patient, consultation d'origine, TVA, échéance."""

    due_days = forms.IntegerField(
        label=_("Délai de paiement (jours)"),
        initial=30,
        min_value=1,
        max_value=180,
        required=False,
        help_text=_("Échéance calculée à l'émission."),
    )

    class Meta:
        model = Invoice
        fields = ["patient", "consultation", "tax_rate", "notes"]
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, clinic=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.clinic = clinic
        patient_queryset = Patient.objects.none()
        consultation_queryset = Consultation.objects.none()
        if clinic is not None:
            patient_queryset = Patient.objects.filter(clinic=clinic).order_by(
                "last_name", "first_name"
            )
            consultation_queryset = Consultation.objects.filter(clinic=clinic).order_by(
                "-created_at"
            )
        self.fields["patient"].queryset = patient_queryset
        self.fields["consultation"].queryset = consultation_queryset
        self.fields["consultation"].required = False
        self.fields["tax_rate"].help_text = _("0,20 = 20 % ; 0 = exonéré.")

    def clean_tax_rate(self):
        value = self.cleaned_data.get("tax_rate")
        if value is None:
            return Decimal("0")
        if value < 0 or value > 1:
            raise forms.ValidationError(_("Le taux de TVA est un ratio entre 0 et 1."))
        return value


class InvoiceLineForm(forms.Form):
    """Ligne de prestation saisie manuellement (formset dynamique)."""

    label = forms.CharField(label=_("Prestation"), max_length=160, required=False)
    quantity = forms.DecimalField(
        label=_("Qté"),
        min_value=Decimal("0.01"),
        max_digits=6,
        decimal_places=2,
        initial=Decimal("1"),
        required=False,
        localize=True,
    )
    unit_price = forms.DecimalField(
        label=_("Prix unitaire"),
        min_value=Decimal("0"),
        max_digits=10,
        decimal_places=2,
        required=False,
        localize=True,
    )

    def cleaned_line(self) -> dict | None:
        """Ligne exploitable, ou ``None`` pour une rangée laissée vide."""
        label = (self.cleaned_data.get("label") or "").strip()
        quantity = self.cleaned_data.get("quantity") or Decimal("1")
        unit_price = self.cleaned_data.get("unit_price") or Decimal("0")
        if not label and unit_price == 0:
            return None
        if not label:
            raise forms.ValidationError(_("Chaque ligne exige une prestation."))
        return {"label": label, "quantity": quantity, "unit_price": unit_price}


InvoiceLineFormSet = formset_factory(InvoiceLineForm, extra=5, can_delete=False)

#: Édition d'un brouillon : mêmes lignes, attachées à la facture.
InvoiceLineModelFormSet = modelformset_factory(
    InvoiceLine,
    fields=["label", "quantity", "unit_price"],
    extra=0,
)


class PaymentForm(forms.ModelForm):
    """Encaissement d'un règlement (partiel ou solde)."""

    class Meta:
        model = Payment
        fields = ["amount", "method", "paid_at", "reference"]
        widgets = {
            "paid_at": forms.DateTimeInput(
                attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"
            ),
        }

    def __init__(self, *args, invoice=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.invoice = invoice
        if invoice is not None and self.instance.pk is None:
            self.fields["amount"].initial = invoice.amount_due
        self.fields["method"].initial = Payment.Method.CASH

    def clean_amount(self):
        amount = self.cleaned_data.get("amount")
        if amount is None:
            return amount
        if amount <= 0:
            raise forms.ValidationError(_("Le montant doit être strictement positif."))
        if self.invoice is not None and amount > self.invoice.amount_due:
            raise forms.ValidationError(
                _("Le règlement dépasse le reste à payer (%(reste)s).")
                % {"reste": f"{self.invoice.amount_due:.2f}"}
            )
        return amount


class InvoiceFilterForm(forms.Form):
    """Filtres de la liste : recherche, état, impayés, période."""

    q = forms.CharField(
        label=_("Recherche"),
        required=False,
        widget=forms.TextInput(
            attrs={"placeholder": _("Nom, téléphone, référence de facture…")}
        ),
    )
    status = forms.ChoiceField(
        label=_("État"),
        required=False,
        choices=[("", _("Tous"))]
        + [(value, label) for value, label in Invoice.Status.choices],
    )
    impayees = forms.ChoiceField(
        label=_("Règlement"),
        required=False,
        choices=[
            ("", _("Toutes")),
            ("dues", _("Impayées échues")),
            ("en-cours", _("À encaisser")),
            ("payees", _("Payées")),
        ],
    )


def parse_day(value, default=None):
    """``AAAA-MM-JJ`` -> date ; valeur absente ou invalide -> ``default``."""
    from datetime import datetime

    if not value:
        return default
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return default
