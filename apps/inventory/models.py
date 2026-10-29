"""Stock et produits (jalon 6).

Chaque clinique gère son catalogue de produits (médicaments, consommables,
matériel, vente libre) et les mouvements qui l'affectent. La quantité en stock
(``stock_quantity``) est dénormalisée et tenue à jour par les mouvements,
sous verrou pessimiste — voir ``apps.inventory.services`` pour les règles :

- référence ``PRD-AAAA-00001`` par clinique ;
- tout changement de stock passe par un mouvement (entrée, sortie, ajustement) ;
- une sortie supérieure au stock disponible est refusée ;
- le seuil ``low_stock_threshold`` alimente les alertes « stock bas ».
"""

from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.common.models import BaseModel, CommentMixin
from apps.common.numbers import next_sequence


class Product(BaseModel, CommentMixin):
    """Article du catalogue de la clinique, avec son stock courant."""

    class Category(models.TextChoices):
        MEDICINE = "MEDICINE", _("Médicament")
        CONSUMABLE = "CONSUMABLE", _("Consommable")
        EQUIPMENT = "EQUIPMENT", _("Matériel")
        RETAIL = "RETAIL", _("Vente libre")

    reference = models.CharField(
        _("référence"),
        max_length=32,
        blank=True,
        editable=False,
        help_text=_("Référence automatique (PRD-AAAA-00001)."),
    )
    name = models.CharField(_("désignation"), max_length=160)
    category = models.CharField(_("catégorie"), max_length=16, choices=Category.choices)
    barcode = models.CharField(_("code-barres"), max_length=40, blank=True, default="")
    unit = models.CharField(
        _("unité"),
        max_length=20,
        default=_("unité"),
        help_text=_("Boîte, flacon, sachet, mL…"),
    )
    purchase_price = models.DecimalField(
        _("prix d'achat"),
        max_digits=10,
        decimal_places=2,
        default=Decimal("0"),
        help_text=_("Coût unitaire d'acquisition."),
    )
    sale_price = models.DecimalField(
        _("prix de vente"), max_digits=10, decimal_places=2, default=Decimal("0")
    )
    stock_quantity = models.DecimalField(
        _("quantité en stock"),
        max_digits=10,
        decimal_places=2,
        default=Decimal("0"),
        editable=False,
        help_text=_("Tenue à jour par les mouvements ; non modifiable à la main."),
    )
    low_stock_threshold = models.DecimalField(
        _("seuil d'alerte"),
        max_digits=10,
        decimal_places=2,
        default=Decimal("5"),
        help_text=_("En dessous de ce niveau, le produit figure en stock bas."),
    )

    class Meta:
        verbose_name = _("produit")
        verbose_name_plural = _("produits")
        ordering = ["name"]
        indexes = [
            models.Index(fields=["clinic", "name"]),
            models.Index(fields=["clinic", "category"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["clinic", "reference"],
                name="uniq_product_reference_per_clinic",
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.reference or '—'})"

    # -- Persistence ------------------------------------------------------

    def save(self, *args, **kwargs):
        if not self.reference:
            self.reference = next_sequence(self.clinic, "product", "PRD", digits=5)
        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        errors = {}
        if self.purchase_price is not None and self.purchase_price < 0:
            errors["purchase_price"] = _("Le prix d'achat ne peut pas être négatif.")
        if self.sale_price is not None and self.sale_price < 0:
            errors["sale_price"] = _("Le prix de vente ne peut pas être négatif.")
        if self.low_stock_threshold is not None and self.low_stock_threshold < 0:
            errors["low_stock_threshold"] = _(
                "Le seuil d'alerte ne peut pas être négatif."
            )
        if errors:
            raise ValidationError(errors)

    # -- État -------------------------------------------------------------

    @property
    def is_low_stock(self) -> bool:
        return self.stock_quantity <= self.low_stock_threshold

    @property
    def stock_value(self) -> Decimal:
        """Valeur du stock disponible au prix d'achat."""
        return (self.stock_quantity * self.purchase_price).quantize(Decimal("0.01"))

    @property
    def display_name(self) -> str:
        return f"{self.name} [{self.get_category_display()}]"


class StockMovement(BaseModel):
    """Mouvement de stock : entrée, sortie ou ajustement d'inventaire."""

    class Kind(models.TextChoices):
        IN = "IN", _("Entrée")
        OUT = "OUT", _("Sortie")
        ADJUST = "ADJUST", _("Ajustement")

    product = models.ForeignKey(
        Product,
        verbose_name=_("produit"),
        on_delete=models.CASCADE,
        related_name="movements",
    )
    kind = models.CharField(_("type"), max_length=8, choices=Kind.choices)
    quantity = models.DecimalField(
        _("quantité"),
        max_digits=10,
        decimal_places=2,
        help_text=_("Toujours positive ; le sens est porté par le type."),
    )
    stock_before = models.DecimalField(
        _("stock avant"), max_digits=10, decimal_places=2, editable=False
    )
    stock_after = models.DecimalField(
        _("stock après"), max_digits=10, decimal_places=2, editable=False
    )
    reason = models.CharField(
        _("motif"),
        max_length=255,
        blank=True,
        default="",
        help_text=_("Bon de commande, consommation, inventaire, périmé…"),
    )
    moved_at = models.DateTimeField(_("horodaté le"), default=timezone.now)

    class Meta:
        verbose_name = _("mouvement de stock")
        verbose_name_plural = _("mouvements de stock")
        ordering = ["-moved_at", "-created_at"]
        indexes = [
            models.Index(fields=["clinic", "moved_at"]),
            models.Index(fields=["product", "-moved_at"]),
        ]

    def __str__(self):
        return f"{self.get_kind_display()} — {self.product.name} × {self.quantity}"
