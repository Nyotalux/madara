"""Formulaires web du stock (jalon 6)."""

from __future__ import annotations

from decimal import Decimal

from django import forms
from django.utils.translation import gettext_lazy as _

from .models import Product, StockMovement


class ProductForm(forms.ModelForm):
    """Création / modification d'un produit de la clinique."""

    initial_quantity = forms.DecimalField(
        label=_("Quantité initiale"),
        min_value=Decimal("0"),
        max_digits=10,
        decimal_places=2,
        initial=Decimal("0"),
        required=False,
        localize=True,
        help_text=_("À l'entrée, une entrée « stock initial » est enregistrée."),
    )

    class Meta:
        model = Product
        fields = [
            "name",
            "category",
            "barcode",
            "unit",
            "purchase_price",
            "sale_price",
            "low_stock_threshold",
            "notes",
        ]
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["unit"].initial = _("unité")
        if self.instance.pk:
            self.fields.pop("initial_quantity")

    def clean_name(self):
        return (self.cleaned_data.get("name") or "").strip()


class MovementForm(forms.Form):
    """Enregistrement d'un mouvement de stock sur un produit."""

    kind = forms.ChoiceField(
        label=_("Type de mouvement"),
        choices=[
            (StockMovement.Kind.IN, _("Entrée")),
            (StockMovement.Kind.OUT, _("Sortie")),
            (StockMovement.Kind.ADJUST, _("Ajustement (inventaire)")),
        ],
    )
    quantity = forms.DecimalField(
        label=_("Quantité"),
        min_value=Decimal("0"),
        max_digits=10,
        decimal_places=2,
        localize=True,
        help_text=_(
            "Entrée / sortie : quantité concernée ; ajustement : stock final constaté."
        ),
    )
    reason = forms.CharField(
        label=_("Motif"),
        max_length=255,
        required=False,
        widget=forms.TextInput(
            attrs={"placeholder": _("Bon de commande, consommation, périmé…")}
        ),
    )
    moved_at = forms.DateTimeField(
        label=_("Horodaté le"),
        widget=forms.DateTimeInput(
            attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"
        ),
        initial=None,
    )

    def __init__(self, *args, product=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.product = product
        self.fields["kind"].initial = StockMovement.Kind.IN
        if self.fields["moved_at"].initial is None and self.is_bound is False:
            from django.utils import timezone

            local_now = timezone.localtime()
            self.fields["moved_at"].initial = local_now.strftime("%Y-%m-%dT%H:%M")

    def clean_quantity(self):
        quantity = self.cleaned_data.get("quantity")
        if quantity is None:
            return quantity
        if quantity <= 0 and self.cleaned_data.get("kind") != StockMovement.Kind.ADJUST:
            raise forms.ValidationError(_("La quantité doit être strictement positive."))
        if quantity < 0:
            raise forms.ValidationError(_("Le stock final ne peut pas être négatif."))
        kind = self.cleaned_data.get("kind")
        if (
            kind == StockMovement.Kind.OUT
            and self.product is not None
            and quantity > self.product.stock_quantity
        ):
            raise forms.ValidationError(
                _("Sortie impossible : il ne reste que %(stock)s %(unite)s.")
                % {
                    "stock": f"{self.product.stock_quantity:n}",
                    "unite": self.product.unit,
                }
            )
        return quantity


class ProductFilterForm(forms.Form):
    """Filtres de la liste produits."""

    q = forms.CharField(
        label=_("Recherche"),
        required=False,
        widget=forms.TextInput(
            attrs={"placeholder": _("Désignation, référence, code-barres…")}
        ),
    )
    category = forms.ChoiceField(
        label=_("Catégorie"),
        required=False,
        choices=[("", _("Toutes"))]
        + [(value, label) for value, label in Product.Category.choices],
    )
    low_stock = forms.ChoiceField(
        label=_("Alerte"),
        required=False,
        choices=[
            ("", _("Tous")),
            ("1", _("Stock bas")),
        ],
    )
