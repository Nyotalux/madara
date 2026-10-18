"""Points de terminaison API des consultations et ordonnances (namespace ``api``)."""

from __future__ import annotations

from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import status as http_status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from apps.common.clinic import ClinicContextMixin
from apps.common.permissions import (
    CanReadClinicalData,
    CanWriteMedicalData,
    IsActiveMember,
)

from . import services
from .models import Consultation, Prescription, VitalSignsMixin
from .serializers import (
    ConsultationListSerializer,
    ConsultationSerializer,
    PrescriptionSerializer,
)

_TEXT_FIELDS = (
    "history",
    "examination",
    "diagnosis",
    "treatment",
    "follow_up_date",
    "notes",
)


@extend_schema_view(
    list=extend_schema(
        summary="Lister les consultations",
        parameters=[
            OpenApiParameter("status", str, description="Statut de la consultation."),
            OpenApiParameter("kind", str, description="Type de consultation."),
            OpenApiParameter("patient", int, description="Identifiant du patient."),
            OpenApiParameter(
                "practitioner", int, description="Identifiant du praticien."
            ),
        ],
    ),
    retrieve=extend_schema(summary="Consulter une consultation"),
    create=extend_schema(
        summary="Démarrer une consultation (patient/praticien ou rendez-vous)"
    ),
    partial_update=extend_schema(summary="Mettre à jour le compte rendu"),
    update=extend_schema(summary="Mettre à jour le compte rendu (PUT)"),
)
class ConsultationViewSet(ClinicContextMixin, ModelViewSet):
    """Consultations, cloisonnées par la clinique courante.

    Lecture : tout le personnel soignant. Écriture : médecin, infirmier,
    administrateur. Un praticien ne voit et ne modifie que ses consultations.
    """

    permission_classes = [IsActiveMember, CanReadClinicalData]
    filterset_fields = ["status", "kind", "patient", "practitioner"]
    http_method_names = ["get", "post", "put", "patch", "head", "options"]

    def get_permissions(self):
        permissions_list = super().get_permissions()
        if self.action in {"create", "update", "partial_update", "complete", "cancel"}:
            return [*permissions_list, CanWriteMedicalData()]
        return permissions_list

    def get_serializer_class(self):
        if self.action == "list":
            return ConsultationListSerializer
        return ConsultationSerializer

    def get_queryset(self):
        clinic = getattr(self.request, "clinic", None)
        if clinic is None:
            return Consultation.objects.none()
        queryset = Consultation.objects.filter(clinic=clinic).select_related(
            "patient", "practitioner", "practitioner__user", "appointment"
        )
        membership = getattr(self.request, "clinic_membership", None)
        if (
            membership is not None
            and not self.request.user.is_platform_staff
            and membership.role in ("DOCTOR", "NURSE")
        ):
            queryset = queryset.filter(practitioner=membership)
        return queryset.order_by("-started_at")

    def _assert_owner(self, consultation):
        membership = getattr(self.request, "clinic_membership", None)
        if (
            membership is not None
            and not self.request.user.is_platform_staff
            and membership.role in ("DOCTOR", "NURSE")
            and consultation.practitioner_id != membership.pk
        ):
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied("Vous ne pouvez agir que sur vos consultations.")

    def perform_create(self, serializer):
        data = serializer.validated_data
        clinic = getattr(self.request, "clinic", None)
        consultation = services.start_consultation(
            clinic=clinic,
            patient=data["patient"],
            practitioner=data["practitioner"],
            appointment=data.get("appointment"),
            kind=data.get("kind"),
            chief_complaint=data.get("chief_complaint", ""),
            user=self.request.user,
        )
        # Reporte les constantes et le compte rendu éventuels fournis à la création.
        for field in (*VitalSignsMixin.VITAL_FIELDS, *_TEXT_FIELDS):
            if field in data:
                setattr(consultation, field, data[field])
        consultation.full_clean()
        consultation.save()
        serializer.instance = consultation

    def perform_update(self, serializer):
        self._assert_owner(serializer.instance)
        serializer.save()

    @extend_schema(
        request=None,
        responses=ConsultationSerializer,
        summary="Terminer la consultation et le rendez-vous associé",
    )
    @action(detail=True, methods=["post"])
    def complete(self, request, pk=None):
        consultation = self.get_object()
        self._assert_owner(consultation)
        services.complete_consultation(consultation, user=request.user)
        return Response(ConsultationSerializer(consultation).data)

    @extend_schema(
        request=None,
        responses=ConsultationSerializer,
        summary="Annuler une consultation (sans suppression)",
    )
    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        consultation = self.get_object()
        self._assert_owner(consultation)
        services.cancel_consultation(
            consultation, reason=request.data.get("reason", ""), user=request.user
        )
        return Response(ConsultationSerializer(consultation).data)


@extend_schema_view(
    list=extend_schema(
        summary="Lister les ordonnances",
        parameters=[
            OpenApiParameter(
                "consultation", int, description="Filtrer par consultation."
            ),
        ],
    ),
    retrieve=extend_schema(summary="Consulter une ordonnance"),
    create=extend_schema(summary="Rédiger une ordonnance (avec ses lignes)"),
)
class PrescriptionViewSet(ClinicContextMixin, ModelViewSet):
    """Ordonnances et lignes, cloisonnées par la clinique courante."""

    serializer_class = PrescriptionSerializer
    permission_classes = [IsActiveMember, CanReadClinicalData]
    filterset_fields = ["consultation", "patient", "practitioner"]
    http_method_names = ["get", "post", "head", "options"]

    def get_permissions(self):
        permissions_list = super().get_permissions()
        if self.action == "create":
            return [*permissions_list, CanWriteMedicalData()]
        return permissions_list

    def get_queryset(self):
        clinic = getattr(self.request, "clinic", None)
        if clinic is None:
            return Prescription.objects.none()
        queryset = Prescription.objects.filter(clinic=clinic).select_related(
            "patient", "practitioner", "practitioner__user", "consultation"
        )
        membership = getattr(self.request, "clinic_membership", None)
        if (
            membership is not None
            and not self.request.user.is_platform_staff
            and membership.role in ("DOCTOR", "NURSE")
        ):
            queryset = queryset.filter(practitioner=membership)
        return queryset.order_by("-issued_at", "-id")

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        prescription = serializer.save()
        return Response(
            self.get_serializer(prescription).data,
            status=http_status.HTTP_201_CREATED,
        )
