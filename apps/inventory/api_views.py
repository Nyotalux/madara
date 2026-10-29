"""Points de terminaison API du stock (namespace ``api``).

Produits et mouvements, cloisonnés par la clinique courante. La lecture est
ouverte à toute l'équipe ; la gestion est réservée à l'administration, à
l'accueil et à la comptabilité (cf. ``apps.inventory.permissions``). Aucune
suppression : les produits se corrigent par mouvements ou archivage.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import F, Q
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from apps.common.clinic import ClinicContextMixin

from . import services
from .models import Product, StockMovement
from .permissions import CanReadInventory, CanWriteInventory
from .serializers import (
    MovementCreateSerializer,
    ProductCreateSerializer,
    ProductSerializer,
    StockMovementSerializer,
)


@extend_schema_view(
    list=extend_schema(
        summary="Lister les produits",
        parameters=[
            OpenApiParameter("q", str, description="Désignation ou référence."),
            OpenApiParameter(
                "category", str, description="medicine|consumable|equipment|retail."
            ),
            OpenApiParameter(
                "low_stock", bool, description="Uniquement les produits en alerte (1)."
            ),
        ],
    ),
    retrieve=extend_schema(summary="Consulter un produit"),
    create=extend_schema(summary="Créer un produit (quantité initiale optionnelle)"),
)
class ProductViewSet(ClinicContextMixin, ModelViewSet):
    """Produits de la clinique courante.

    Lectures : toute l'équipe. Création et modification : ADMIN, RECEPTION,
    ACCOUNTANT. La suppression est refusée — on archive un produit.
    """

    serializer_class = ProductSerializer
    permission_classes = [CanReadInventory]

    def get_permissions(self):
        permissions_list = super().get_permissions()
        if self.action in {"create", "update", "partial_update"}:
            return [*permissions_list, CanWriteInventory()]
        return permissions_list

    def get_serializer_class(self):
        if self.action == "create":
            return ProductCreateSerializer
        return ProductSerializer

    def get_queryset(self):
        clinic = getattr(self.request, "clinic", None)
        if clinic is None:
            return Product.objects.none()
        queryset = Product.objects.filter(clinic=clinic)
        params = self.request.query_params
        needle = (params.get("q") or "").strip()
        if len(needle) >= 2:
            queryset = queryset.filter(
                Q(name__icontains=needle)
                | Q(reference__icontains=needle)
                | Q(barcode__icontains=needle)
            )
        category = params.get("category") or ""
        if category:
            queryset = queryset.filter(category=category)
        if params.get("low_stock") in ("1", "true", "True"):
            queryset = queryset.filter(stock_quantity__lte=F("low_stock_threshold"))
        return queryset.order_by("name")

    def perform_create(self, serializer):
        data = serializer.validated_data
        clinic = getattr(self.request, "clinic", None)
        try:
            product = services.create_product(
                clinic=clinic,
                name=data.get("name") or "",
                category=data.get("category"),
                unit=data.get("unit") or "",
                barcode=data.get("barcode") or "",
                purchase_price=data.get("purchase_price"),
                sale_price=data.get("sale_price"),
                low_stock_threshold=data.get("low_stock_threshold"),
                notes=data.get("notes") or "",
                initial_quantity=data.get("initial_quantity"),
                user=self.request.user,
            )
        except DjangoValidationError:
            raise
        serializer.instance = product

    def perform_update(self, serializer):
        # Le stock ne se modifie pas à la main : les prix et repères oui.
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
        serializer.save(update_fields=fields)

    def perform_destroy(self, instance):
        raise DjangoValidationError(_("Un produit ne se supprime pas : archivez-le."))

    @extend_schema(
        responses=StockMovementSerializer(many=True),
        summary="Historique des mouvements du produit",
    )
    @action(detail=True, methods=["get"])
    def movements(self, request, pk=None):
        product = self.get_object()
        return Response(StockMovementSerializer(product.movements.all(), many=True).data)


class StockMovementViewSet(ClinicContextMixin, ModelViewSet):
    """Mouvements de stock : consultation et enregistrement.

    L'écriture passe par ``apps.inventory.services.record_movement`` : le
    solde est verrouillé au moment de l'écriture et la sortie d'une quantité
    supérieure au stock est refusée.
    """

    serializer_class = StockMovementSerializer
    permission_classes = [CanReadInventory]
    http_method_names = ["get", "post", "head", "options"]

    def get_permissions(self):
        permissions_list = super().get_permissions()
        if self.action == "create":
            return [*permissions_list, CanWriteInventory()]
        return permissions_list

    def get_queryset(self):
        clinic = getattr(self.request, "clinic", None)
        if clinic is None:
            return StockMovement.objects.none()
        queryset = StockMovement.objects.filter(clinic=clinic).select_related("product")
        params = self.request.query_params
        product_id = params.get("product")
        if product_id:
            queryset = queryset.filter(product_id=product_id)
        kind = params.get("kind") or ""
        if kind in StockMovement.Kind.values:
            queryset = queryset.filter(kind=kind)
        return queryset.order_by("-moved_at", "-created_at")

    def get_serializer_class(self):
        if self.request.method == "POST":
            return MovementCreateSerializer
        return StockMovementSerializer

    def create(self, request, *args, **kwargs):
        serializer = MovementCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        clinic = getattr(request, "clinic", None)
        if getattr(data["product"], "clinic_id", None) != getattr(clinic, "pk", None):
            raise DjangoValidationError(
                _("Le produit n'appartient pas à cette clinique.")
            )
        try:
            movement = services.record_movement(
                clinic,
                data["product"],
                kind=data["kind"],
                quantity=data["quantity"],
                reason=data.get("reason") or "",
                moved_at=data.get("moved_at"),
                user=request.user,
            )
        except DjangoValidationError:
            raise
        return Response(StockMovementSerializer(movement).data, status=201)
