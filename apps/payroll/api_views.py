"""Points de terminaison API de la paie (namespace ``api``).

Bulletins et primes, cloisonnés par la clinique courante. La paie est
réservée à l'administration de la clinique et à la comptabilité
(cf. ``apps.payroll.permissions``) : les autres rôles n'y accèdent pas.
La suppression est refusée : un bulletin s'annule, il ne disparaît pas.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from apps.common.clinic import ClinicContextMixin

from . import services
from .models import Bonus, Payslip
from .permissions import CanReadPayroll, CanWritePayroll
from .serializers import (
    BonusCreateSerializer,
    BonusSerializer,
    PayslipSerializer,
    PayslipUpdateSerializer,
)


@extend_schema_view(
    list=extend_schema(
        summary="Lister les bulletins de salaire",
        parameters=[
            OpenApiParameter("period", str, description="Période AAAA-MM."),
            OpenApiParameter("status", str, description="draft|paid|cancelled."),
            OpenApiParameter("membership", str, description="Identifiant du membre."),
        ],
    ),
    retrieve=extend_schema(summary="Consulter un bulletin"),
    partial_update=extend_schema(summary="Ajuster un bulletin brouillon"),
)
class PayslipViewSet(ClinicContextMixin, ModelViewSet):
    """Bulletins de salaire de la clinique courante (ADMIN, ACCOUNTANT).

    Création : ``POST /api/v1/payslips/generate/`` (période complète) ou
    ajustement des brouillons ; paiement via ``…/{id}/pay/``.
    """

    serializer_class = PayslipSerializer
    permission_classes = [CanReadPayroll]
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_permissions(self):
        permissions_list = super().get_permissions()
        if self.action in {"partial_update", "pay", "cancel", "generate"}:
            return [*permissions_list, CanWritePayroll()]
        return permissions_list

    def get_serializer_class(self):
        if self.action == "partial_update":
            return PayslipUpdateSerializer
        return PayslipSerializer

    def get_queryset(self):
        clinic = getattr(self.request, "clinic", None)
        if clinic is None:
            return Payslip.objects.none()
        queryset = Payslip.objects.filter(clinic=clinic).select_related(
            "membership", "membership__user"
        )
        params = self.request.query_params
        period = (params.get("period") or "").strip()
        if period:
            queryset = queryset.filter(period=period)
        payslip_status = params.get("status") or ""
        if payslip_status in Payslip.Status.values:
            queryset = queryset.filter(status=payslip_status)
        membership_id = params.get("membership")
        if membership_id:
            queryset = queryset.filter(membership_id=membership_id)
        return queryset.order_by("-period", "created_at")

    def perform_create(self, serializer):
        raise DjangoValidationError(
            _(
                "Les bulletins d'une période sont établis via "
                "POST /api/v1/payslips/generate/ plutôt qu'un à la main."
            )
        )

    def perform_update(self, serializer):
        try:
            services.update_payslip(
                serializer.instance,
                user=self.request.user,
                **{
                    "base_amount": serializer.validated_data.get("base_amount"),
                    "bonus_amount": serializer.validated_data.get("bonus_amount"),
                    "deduction_amount": serializer.validated_data.get("deduction_amount"),
                    "notes": serializer.validated_data.get("notes"),
                },
            )
        except DjangoValidationError:
            raise
        serializer.instance.refresh_from_db()

    def perform_destroy(self, instance):
        raise DjangoValidationError(
            _("Un bulletin de salaire ne se supprime pas : utilisez l'annulation.")
        )

    @extend_schema(
        request={"type": "object", "properties": {"period": {"type": "string"}}},
        responses=PayslipSerializer(many=True),
        summary="Établir les bulletins manquants de la période",
    )
    @action(detail=False, methods=["post"])
    def generate(self, request):
        clinic = getattr(request, "clinic", None)
        period = services.normalize_period(
            request.data.get("period") or services.current_period()
        )
        payslips = services.generate_payslips(clinic, period=period, user=request.user)
        return Response(
            {
                "period": period,
                "created": len(payslips),
                "payslips": PayslipSerializer(payslips, many=True).data,
            }
        )

    @extend_schema(
        request=None,
        responses=PayslipSerializer,
        summary="Payer un bulletin (définitif)",
    )
    @action(detail=True, methods=["post"])
    def pay(self, request, pk=None):
        payslip = self.get_object()
        try:
            services.pay_payslip(payslip, user=request.user)
        except DjangoValidationError:
            raise
        return Response(PayslipSerializer(payslip).data)

    @extend_schema(
        request={"type": "object", "properties": {"reason": {"type": "string"}}},
        responses=PayslipSerializer,
        summary="Annuler un bulletin impayé",
    )
    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        payslip = self.get_object()
        try:
            services.cancel_payslip(
                payslip,
                reason=(request.data.get("reason") or "").strip(),
                user=request.user,
            )
        except DjangoValidationError:
            raise
        return Response(PayslipSerializer(payslip).data)


@extend_schema_view(
    list=extend_schema(
        summary="Lister les primes et retenues",
        parameters=[
            OpenApiParameter("period", str, description="Période AAAA-MM."),
            OpenApiParameter("membership", str, description="Identifiant du membre."),
            OpenApiParameter("kind", str, description="BONUS|DEDUCTION."),
        ],
    ),
    create=extend_schema(summary="Ajouter une prime ou une retenue (DELETE via retirer)"),
)
class BonusViewSet(ClinicContextMixin, ModelViewSet):
    """Primes et retenues de la clinique courante (ADMIN, ACCOUNTANT).

    La suppression directe est refusée via l'API REST ; utiliser
    ``retirer`` côté web. Pour tenir le modèle REST simple, ``DELETE``
    interpelle ``services.remove_bonus``.
    """

    serializer_class = BonusSerializer
    permission_classes = [CanReadPayroll]

    def get_permissions(self):
        permissions_list = super().get_permissions()
        if self.action in {"create", "destroy"}:
            return [*permissions_list, CanWritePayroll()]
        return permissions_list

    def get_serializer_class(self):
        if self.action == "create":
            return BonusCreateSerializer
        return BonusSerializer

    def get_queryset(self):
        clinic = getattr(self.request, "clinic", None)
        if clinic is None:
            return Bonus.objects.none()
        queryset = Bonus.objects.filter(clinic=clinic).select_related(
            "membership", "membership__user"
        )
        params = self.request.query_params
        period = (params.get("period") or "").strip()
        if period:
            queryset = queryset.filter(period=period)
        membership_id = params.get("membership")
        if membership_id:
            queryset = queryset.filter(membership_id=membership_id)
        kind = params.get("kind") or ""
        if kind in Bonus.Kind.values:
            queryset = queryset.filter(kind=kind)
        return queryset.order_by("-period", "-created_at")

    def perform_create(self, serializer):
        clinic = getattr(self.request, "clinic", None)
        try:
            services.add_bonus(
                clinic,
                serializer.validated_data["membership"],
                kind=serializer.validated_data["kind"],
                label=serializer.validated_data["label"],
                amount=serializer.validated_data["amount"],
                period=serializer.validated_data.get("period"),
                user=self.request.user,
            )
        except DjangoValidationError:
            raise

    def perform_destroy(self, instance):
        # Une prime d'un bulletin payé est figée ; sinon erreur de saisie.
        try:
            services.remove_bonus(instance, user=self.request.user)
        except DjangoValidationError as error:
            raise DjangoValidationError(
                str(getattr(error, "message", _("Retrait refusé.")))
            ) from error
