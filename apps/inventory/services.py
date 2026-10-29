"""Règles de stock : produits et mouvements (jalon 6).

Fonctions module-level comme dans les autres apps : les vues web, l'API et les
tâches de fond passent toutes par ici. Tout changement de quantité passe par un
mouvement enregistré sous verrou pessimiste : deux sorties simultanées ne
peuvent pas faire descendre le stock sous zéro, et le solde reste toujours
reconciliable avec l'historique (``stock_before`` / ``stock_after``).
"""

from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils.timezone import now
from django.utils.translation import gettext_lazy as _

from .models import Product, StockMovement

#: Rôles habilités à consulter le stock.
INVENTORY_READ_ROLES = ("ADMIN", "RECEPTION", "DOCTOR", "NURSE", "ACCOUNTANT")
#: Rôles habilités à gérer produits et mouvements (les praticiens consultent).
INVENTORY_WRITE_ROLES = ("ADMIN", "RECEPTION", "ACCOUNTANT")


@transaction.atomic
def create_product(
    *,
    clinic,
    name: str,
    category: str,
    unit: str = "",
    barcode: str = "",
    purchase_price=None,
    sale_price=None,
    low_stock_threshold=None,
    notes: str = "",
    initial_quantity=None,
    user=None,
) -> Product:
    """Crée un produit ; une quantité initiale crée l'entrée d'origine."""
    if not (name or "").strip():
        raise ValidationError({"name": _("La désignation est obligatoire.")})
    if category not in Product.Category.values:
        raise ValidationError({"category": _("Catégorie inconnue.")})

    product = Product(
        clinic=clinic,
        name=(name or "").strip()[:160],
        category=category,
        unit=(unit or "").strip()[:20] or "unité",
        barcode=(barcode or "").strip()[:40],
        purchase_price=_as_price(purchase_price),
        sale_price=_as_price(sale_price),
        low_stock_threshold=(
            max(Decimal(str(low_stock_threshold)), Decimal("0"))
            if low_stock_threshold is not None
            else Decimal("5")
        ),
        notes=notes or "",
        created_by=user if getattr(user, "is_authenticated", False) else None,
    )
    product.full_clean()
    product.save()

    initial = _positive_quantity(initial_quantity, "initial_quantity")
    if initial is not None:
        record_movement(
            clinic,
            product,
            kind=StockMovement.Kind.IN,
            quantity=initial,
            reason=_("Stock initial"),
            user=user,
        )
        product.refresh_from_db()  # solde mis à jour par le mouvement
    return product


@transaction.atomic
def record_movement(
    clinic,
    product: Product,
    *,
    kind: str,
    quantity,
    reason: str = "",
    moved_at=None,
    user=None,
) -> StockMovement:
    """Applique un mouvement au stock sous verrou pessimiste.

    Entrée et sortie replissent le stock ; l'ajustement ramène le stock à la
    valeur cible (l'inventaire physique). Quantités négatives refusées : le
    sens est porté par le type de mouvement.
    """
    if product.clinic_id != clinic.pk:
        raise ValidationError(_("Le produit n'appartient pas à cette clinique."))
    if kind not in StockMovement.Kind.values:
        raise ValidationError({"kind": _("Type de mouvement inconnu.")})

    if kind == StockMovement.Kind.ADJUST:
        target = _quantized(quantity, "quantity")
        if target < 0:
            raise ValidationError(
                {"quantity": _("L'ajustement fixe un stock positif ou nul.")}
            )
    else:
        target = _positive_quantity(quantity, "quantity")

    # Verrou pessimiste : la lecture et l'écriture du stock sont atomiques.
    locked = Product.all_objects.select_for_update().get(pk=product.pk)
    before = locked.stock_quantity

    if kind == StockMovement.Kind.IN:
        after = before + target
    elif kind == StockMovement.Kind.OUT:
        after = before - target
        if after < 0:
            raise ValidationError(
                {
                    "quantity": _(
                        "Sortie impossible : il ne reste que %(stock)s %(unite)s."
                    )
                    % {"stock": f"{before:n}", "unite": locked.unit}
                }
            )
    else:
        after = target

    movement = StockMovement(
        clinic=clinic,
        product=locked,
        kind=kind,
        quantity=(abs(after - before) if kind == StockMovement.Kind.ADJUST else target),
        stock_before=before,
        stock_after=after,
        reason=(reason or "").strip()[:255],
        moved_at=moved_at or now(),
        created_by=user if getattr(user, "is_authenticated", False) else None,
    )
    movement.full_clean()
    movement.save()

    locked.stock_quantity = after
    locked.save(update_fields=["stock_quantity", "updated_at"])
    return movement


def low_stock_products(clinic=None):
    """Produits actifs sous leur seuil d'alerte : alertes et rapport quotidien."""
    queryset = Product.objects.filter(stock_quantity__lte=models.F("low_stock_threshold"))
    if clinic is not None:
        queryset = queryset.filter(clinic=clinic)
    return queryset.order_by("name")


# ---------------------------------------------------------------------------
# Aides
# ---------------------------------------------------------------------------


def _as_price(value) -> Decimal:
    price = _quantized(value or "0", "purchase_price")
    if price < 0:
        raise ValidationError({"purchase_price": _("Le prix ne peut pas être négatif.")})
    return price


def _quantized(value, field: str) -> Decimal:
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except Exception:
        raise ValidationError({field: _("La quantité est un nombre positif.")}) from None


def _positive_quantity(value, field: str) -> Decimal | None:
    if value is None or str(value).strip() == "":
        return None
    quantity = _quantized(value, field)
    if quantity <= 0:
        raise ValidationError({field: _("La quantité doit être strictement positive.")})
    return quantity
