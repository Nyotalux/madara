"""Points de terminaison API de la facturation (namespace ``api``).

Factures et règlements, cloisonnés par la clinique courante ; les praticiens
et infirmiers n'y ont pas accès (cf. ``apps.billing.permissions``).
"""

from __future__ import annotations

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Q
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from apps.common.clinic import ClinicContextMixin

from . import services
from .models import Invoice, Payment
from .permissions import CanCancelInvoice, CanReadBilling, CanWriteBilling
from .serializers import (
    InvoiceCreateSerializer,
    InvoicePaymentSerializer,
    InvoiceSerializer,
    PaymentSerializer,
)


@extend_schema_view(
    list=extend_schema(
        summary="Lister les factures",
        parameters=[
            OpenApiParameter(
                "status", str, description="draft|issued|partial|paid|cancelled."
            ),
            OpenApiParameter("q", str, description="Référence ou patient."),
            OpenApiParameter(
                "overdue", bool, description="Uniquement les impayés échues (1)."
            ),
        ],
    ),
    retrieve=extend_schema(summary="Consulter une facture"),
    create=extend_schema(
        summary="Créer une facture (lignes imbriquées, émission optionnelle)"
    ),
)
class InvoiceViewSet(ClinicContextMixin, ModelViewSet):
    """Factures de la clinique courante.

    Lecture et écriture : ADMIN, RECEPTION, ACCOUNTANT. L'annulation est
    réservée à ADMIN et ACCOUNTANT ; les praticiens n'y ont pas accès.
    """

    serializer_class = InvoiceSerializer
    permission_classes = [CanReadBilling]

    def get_permissions(self):
        permissions_list = super().get_permissions()
        if self.action == "create":
            return [*permissions_list, CanWriteBilling()]
        if self.action in {"pay", "cancel"}:
            return [*permissions_list, CanWriteBilling()]
        return permissions_list

    def get_serializer_class(self):
        if self.action == "create":
            return InvoiceCreateSerializer
        return InvoiceSerializer

    def get_queryset(self):
        clinic = getattr(self.request, "clinic", None)
        if clinic is None:
            return Invoice.objects.none()
        queryset = Invoice.objects.filter(clinic=clinic).select_related("patient")
        params = self.request.query_params
        needle = (params.get("q") or "").strip()
        if len(needle) >= 2:
            queryset = queryset.filter(
                Q(reference__icontains=needle)
                | Q(patient__first_name__icontains=needle)
                | Q(patient__last_name__icontains=needle)
                | Q(patient__phone__icontains=needle)
            )
        invoice_status = params.get("status") or ""
        if invoice_status in Invoice.Status.values:
            queryset = queryset.filter(status=invoice_status)
        if params.get("overdue") in ("1", "true", "True"):
            queryset = queryset.filter(
                status__in=(Invoice.Status.ISSUED, Invoice.Status.PARTIAL),
                due_at__lt=services.timezone.localdate(),
            )
        return queryset.order_by("-created_at")

    def perform_create(self, serializer):
        data = serializer.validated_data
        clinic = getattr(self.request, "clinic", None)
        try:
            invoice = services.create_invoice(
                clinic=clinic,
                patient=data["patient"],
                lines=data.get("lines") or [],
                consultation=data.get("consultation"),
                tax_rate=data.get("tax_rate"),
                due_days=data.get("due_days"),
                status=(
                    Invoice.Status.ISSUED if data.get("emit") else Invoice.Status.DRAFT
                ),
                notes=data.get("notes") or "",
                user=self.request.user,
            )
        except DjangoValidationError:
            raise
        serializer.instance = invoice

    def perform_update(self, serializer):
        invoice = serializer.instance
        if invoice.status != Invoice.Status.DRAFT:
            raise DjangoValidationError(_("Seul un brouillon est modifiable."))
        serializer.save()

    def perform_destroy(self, instance):
        # Le modèle est en archivage logique ; la facturation annule au lieu
        # de détruire (l'historique comptable est conservé).
        raise DjangoValidationError(
            _("Une facture ne se supprime pas : utilisez l'annulation.")
        )

    @extend_schema(
        request=InvoicePaymentSerializer,
        responses=InvoiceSerializer,
        summary="Encaisser un règlement (partiel ou solde)",
    )
    @action(detail=True, methods=["post"])
    def pay(self, request, pk=None):
        invoice = self.get_object()
        serializer = InvoicePaymentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            services.add_payment(
                invoice,
                amount=data["amount"],
                method=data["method"],
                paid_at=data.get("paid_at"),
                reference=data.get("reference") or "",
                user=request.user,
            )
        except DjangoValidationError:
            raise
        return Response(InvoiceSerializer(invoice).data)

    @extend_schema(
        request={"type": "object", "properties": {"reason": {"type": "string"}}},
        responses=InvoiceSerializer,
        summary="Annuler une facture (sans règlement)",
    )
    @action(detail=True, methods=["post"], permission_classes=[CanCancelInvoice])
    def cancel(self, request, pk=None):
        invoice = self.get_object()
        try:
            services.cancel_invoice(
                invoice,
                reason=(request.data.get("reason") or "").strip(),
                user=request.user,
            )
        except DjangoValidationError:
            raise
        return Response(InvoiceSerializer(invoice).data)

    @extend_schema(
        responses=PaymentSerializer(many=True),
        summary="Historique des règlements de la facture",
    )
    @action(detail=True, methods=["get"])
    def payments(self, request, pk=None):
        invoice = self.get_object()
        return Response(PaymentSerializer(invoice.payments.all(), many=True).data)


class PaymentViewSet(ClinicContextMixin, ModelViewSet):
    """Règlements enregistrés (lecture seule ; l'encaissement passe par la facture)."""

    serializer_class = PaymentSerializer
    permission_classes = [CanReadBilling]
    http_method_names = ["get", "head", "options"]

    def get_queryset(self):
        clinic = getattr(self.request, "clinic", None)
        if clinic is None:
            return Payment.objects.none()
        queryset = Payment.objects.filter(clinic=clinic).select_related(
            "invoice", "invoice__patient"
        )
        invoice_id = self.request.query_params.get("invoice")
        if invoice_id:
            queryset = queryset.filter(invoice_id=invoice_id)
        return queryset.order_by("-paid_at", "-created_at")
