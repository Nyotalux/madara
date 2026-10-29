"""Vues web du stock (jalon 6).

Liste filtrable avec compteurs et alertes, fiche produit avec historique des
mouvements, saisie de mouvement (entrée, sortie, ajustement d'inventaire).
La gestion est réservée à ADMIN, RECEPTION et ACCOUNTANT ; toute l'équipe
consulte (cf. ``apps.inventory.services``).
"""

from __future__ import annotations

from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import F, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _

from apps.common.decorators import require_clinic, require_roles
from apps.common.pagination import paginate

from .forms import MovementForm, ProductFilterForm, ProductForm
from .models import Product, StockMovement
from .services import (
    INVENTORY_READ_ROLES,
    INVENTORY_WRITE_ROLES,
    create_product,
    low_stock_products,
    record_movement,
)

MOVEMENT_SCOPES = {
    "entrees": (StockMovement.Kind.IN,),
    "sorties": (StockMovement.Kind.OUT,),
    "ajustements": (StockMovement.Kind.ADJUST,),
}


# ---------------------------------------------------------------------------
# Lecture
# ---------------------------------------------------------------------------


@login_required
@require_clinic
@require_roles(*INVENTORY_READ_ROLES)
def product_list(request):
    """Catalogue de la clinique : recherche, catégorie, alertes de stock."""
    clinic = request.clinic
    products = Product.objects.filter(clinic=clinic)

    form = ProductFilterForm(request.GET or None)
    needle = (request.GET.get("q") or "").strip()
    if len(needle) >= 2:
        products = products.filter(
            Q(name__icontains=needle)
            | Q(reference__icontains=needle)
            | Q(barcode__icontains=needle)
        )
    category = request.GET.get("category") or ""
    if category in Product.Category.values:
        products = products.filter(category=category)
    if (request.GET.get("low_stock") or "") == "1":
        products = products.filter(stock_quantity__lte=F("low_stock_threshold"))

    page = paginate(request, products)
    stats = {
        "references": products.count(),
        "low_stock": low_stock_products(clinic).count(),
        "stock_value": products.aggregate(
            value=Sum(F("stock_quantity") * F("purchase_price"))
        )["value"]
        or 0,
        "out_of_stock": products.filter(stock_quantity__lte=0).count(),
    }
    context = {
        "form": form,
        "products": page["page_obj"].object_list,
        "result_count": products.count(),
        "stats": stats,
        "today": date.today(),
        **page,
    }
    return render(request, "inventory/product_list.html", context)


@login_required
@require_clinic
@require_roles(*INVENTORY_READ_ROLES)
def product_detail(request, pk: int):
    """Fiche produit : repères, stock, mouvements récents."""
    product = _product_or_404(request, pk)
    movements = product.movements.all()[:20]
    context = {
        "product": product,
        "movements": movements,
        "can_write": _has_role(request, INVENTORY_WRITE_ROLES),
    }
    return render(request, "inventory/product_detail.html", context)


@login_required
@require_clinic
@require_roles(*INVENTORY_READ_ROLES)
def movement_list(request):
    """Journal des mouvements de la clinique, filtrable."""
    clinic = request.clinic
    movements = StockMovement.objects.filter(clinic=clinic).select_related("product")

    kind = request.GET.get("type") or ""
    if kind in MOVEMENT_SCOPES:
        movements = movements.filter(kind__in=MOVEMENT_SCOPES[kind])
    needle = (request.GET.get("q") or "").strip()
    if len(needle) >= 2:
        movements = movements.filter(
            Q(product__name__icontains=needle)
            | Q(product__reference__icontains=needle)
            | Q(reason__icontains=needle)
        )
    day = request.GET.get("date") or ""
    if day:
        try:
            as_date = date.fromisoformat(day)
        except ValueError:
            pass
        else:
            movements = movements.filter(moved_at__date=as_date)

    page = paginate(request, movements)
    context = {
        "movements": page["page_obj"].object_list,
        "result_count": movements.count(),
        "stocks": MOVEMENT_SCOPES,
        **page,
    }
    return render(request, "inventory/movement_list.html", context)


# ---------------------------------------------------------------------------
# Écriture
# ---------------------------------------------------------------------------


@login_required
@require_clinic
@require_roles(*INVENTORY_WRITE_ROLES)
def product_create(request):
    """Nouveau produit avec sa quantité initiale éventuelle."""
    form = ProductForm()

    if request.method == "POST":
        form = ProductForm(request.POST)
        if form.is_valid():
            data = form.cleaned_data
            try:
                product = create_product(
                    clinic=request.clinic,
                    name=data["name"],
                    category=data["category"],
                    unit=data["unit"],
                    barcode=data.get("barcode") or "",
                    purchase_price=data.get("purchase_price") or 0,
                    sale_price=data.get("sale_price") or 0,
                    low_stock_threshold=data.get("low_stock_threshold"),
                    notes=data.get("notes") or "",
                    initial_quantity=data.get("initial_quantity") or 0,
                    user=request.user,
                )
            except ValidationError as error:
                messages.error(request, _format_error(error))
            else:
                messages.success(
                    request,
                    _("Produit %(reference)s créé.") % {"reference": product.reference},
                )
                return redirect("web:product-detail", pk=product.pk)
        messages.error(request, _("Vérifiez le formulaire de produit."))

    return render(request, "inventory/product_form.html", {"form": form})


@login_required
@require_clinic
@require_roles(*INVENTORY_WRITE_ROLES)
def product_edit(request, pk: int):
    """Modification d'un produit : repères et prix, jamais le stock."""
    product = _product_or_404(request, pk)
    if request.method == "POST":
        form = ProductForm(request.POST, instance=product)
        if form.is_valid():
            form.save()
            messages.success(request, _("Produit mis à jour."))
            return redirect("web:product-detail", pk=product.pk)
        messages.error(request, _("Vérifiez le formulaire."))
    else:
        form = ProductForm(instance=product)

    return render(
        request,
        "inventory/product_form.html",
        {"form": form, "product": product, "editing": True},
    )


@login_required
@require_clinic
@require_roles(*INVENTORY_WRITE_ROLES)
def product_archive(request, pk: int):
    """Archive un produit (plus distribué, il reste historisé)."""
    product = _product_or_404(request, pk)
    if request.method == "POST":
        product.archive((request.POST.get("reason") or "").strip())
        messages.success(
            request,
            _("Produit %(reference)s archivé.") % {"reference": product.reference},
        )
    return redirect("web:product-detail", pk=product.pk)


@login_required
@require_clinic
@require_roles(*INVENTORY_WRITE_ROLES)
def product_restore(request, pk: int):
    """Restaure un produit archivé."""
    product = _product_or_404(request, pk)
    if request.method == "POST":
        product.restore()
        messages.success(request, _("Produit restauré."))
    return redirect("web:product-detail", pk=product.pk)


@login_required
@require_clinic
@require_roles(*INVENTORY_WRITE_ROLES)
def product_movement(request, pk: int):
    """Enregistre une entrée, une sortie ou un ajustement d'inventaire."""
    product = _product_or_404(request, pk)
    form = MovementForm(request.POST or None, product=product)
    if request.method == "POST":
        if not product.is_active:
            messages.error(
                request,
                _("Le produit %(reference)s est archivé : restaurez-le d'abord.")
                % {"reference": product.reference},
            )
            return redirect("web:product-detail", pk=product.pk)
        if form.is_valid():
            try:
                movement = record_movement(
                    request.clinic,
                    product,
                    kind=form.cleaned_data["kind"],
                    quantity=form.cleaned_data["quantity"],
                    reason=form.cleaned_data.get("reason") or "",
                    user=request.user,
                )
            except ValidationError as error:
                messages.error(request, _format_error(error))
            else:
                messages.success(
                    request,
                    _("%(type)s de %(q)s %(unite)s enregistré.")
                    % {
                        "type": movement.get_kind_display(),
                        "q": f"{movement.quantity:n}",
                        "unite": product.unit,
                    },
                )
                return redirect("web:product-detail", pk=product.pk)

    return render(
        request,
        "inventory/movement_form.html",
        {"form": form, "product": product},
    )


# ---------------------------------------------------------------------------
# Aides
# ---------------------------------------------------------------------------


def _product_or_404(request, pk: int) -> Product:
    return get_object_or_404(Product.objects.filter(clinic=request.clinic), pk=pk)


def _has_role(request, roles) -> bool:
    if request.user.is_platform_staff:
        return True
    membership = getattr(request, "clinic_membership", None)
    return bool(membership and membership.role in roles)


def _format_error(error: ValidationError) -> str:
    if hasattr(error, "message_dict"):
        parts = []
        for _field, messages_list in error.message_dict.items():
            parts.extend(str(message) for message in messages_list)
        return " ".join(parts)
    return " ".join(str(message) for message in error.messages)
