"""Points de terminaison API du dossier médical (namespace ``api``)."""

from __future__ import annotations

from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import status as http_status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from apps.common.clinic import ClinicContextMixin
from apps.common.permissions import (
    CanReadClinicalData,
    CanWriteClinicalData,
    IsActiveMember,
)

from .models import MedicalDocument, MedicalRecord, MedicalRecordShare
from .serializers import (
    MedicalDocumentSerializer,
    MedicalRecordListSerializer,
    MedicalRecordSerializer,
    MedicalRecordShareSerializer,
)


@extend_schema_view(
    list=extend_schema(
        summary="Lister les dossiers médicaux de la clinique courante",
        parameters=[
            OpenApiParameter("patient", int, description="Filtrer par patient."),
        ],
    ),
    retrieve=extend_schema(summary="Consulter un dossier médical"),
    partial_update=extend_schema(summary="Mettre à jour les antécédents"),
    update=extend_schema(summary="Remplacer les antécédents (PUT)"),
)
class MedicalRecordViewSet(ClinicContextMixin, ModelViewSet):
    """Dossiers médicaux, cloisonnés par la clinique courante.

    Les dossiers sont créés automatiquement (côté patient) : l'API les consulte
    et met à jour les antécédents.
    """

    permission_classes = [IsActiveMember, CanReadClinicalData]
    filterset_fields = ["patient"]
    search_fields = [
        "reference",
        "patient__first_name",
        "patient__last_name",
        "patient__reference",
    ]
    http_method_names = ["get", "put", "patch", "head", "options"]

    def get_serializer_class(self):
        if self.action == "list":
            return MedicalRecordListSerializer
        return MedicalRecordSerializer

    def get_permissions(self):
        permissions_list = super().get_permissions()
        if self.action in {"update", "partial_update"}:
            return [*permissions_list, CanWriteClinicalData()]
        return permissions_list

    def get_queryset(self):
        clinic = getattr(self.request, "clinic", None)
        if clinic is None:
            return MedicalRecord.objects.none()
        return (
            MedicalRecord.objects.filter(clinic=clinic)
            .select_related("patient", "reviewed_by", "reviewed_by__user")
            .order_by("-created_at")
        )


@extend_schema_view(
    list=extend_schema(
        summary="Lister les documents médicaux",
        parameters=[
            OpenApiParameter("record", int, description="Filtrer par dossier."),
            OpenApiParameter("category", str, description="Filtrer par catégorie."),
        ],
    ),
    retrieve=extend_schema(summary="Consulter un document"),
    create=extend_schema(summary="Déposer un document (multipart)"),
)
class MedicalDocumentViewSet(ClinicContextMixin, ModelViewSet):
    """Pièces du dossier médical (fichiers), cloisonnées par la clinique."""

    serializer_class = MedicalDocumentSerializer
    permission_classes = [IsActiveMember, CanReadClinicalData]
    filterset_fields = ["record", "category"]
    http_method_names = ["get", "post", "head", "options"]

    def get_permissions(self):
        permissions_list = super().get_permissions()
        if self.action == "create":
            return [*permissions_list, CanWriteClinicalData()]
        return permissions_list

    def get_queryset(self):
        clinic = getattr(self.request, "clinic", None)
        if clinic is None:
            return MedicalDocument.objects.none()
        return (
            MedicalDocument.objects.filter(clinic=clinic)
            .select_related("record__patient", "created_by")
            .order_by("-document_date", "-id")
        )

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        document = serializer.save()
        return Response(
            self.get_serializer(document).data,
            status=http_status.HTTP_201_CREATED,
        )


@extend_schema_view(
    list=extend_schema(
        summary="Lister les partages de dossier",
        parameters=[
            OpenApiParameter("record", int, description="Filtrer par dossier."),
        ],
    ),
    retrieve=extend_schema(summary="Consulter un partage"),
    create=extend_schema(summary="Créer un lien de partage temporaire"),
)
class MedicalRecordShareViewSet(ClinicContextMixin, ModelViewSet):
    """Liens de partage à durée limitée, cloisonnés par la clinique courante."""

    serializer_class = MedicalRecordShareSerializer
    permission_classes = [IsActiveMember, CanReadClinicalData]
    filterset_fields = ["record"]
    http_method_names = ["get", "post", "head", "options"]

    def get_permissions(self):
        permissions_list = super().get_permissions()
        if self.action in {"create", "revoke"}:
            return [*permissions_list, CanWriteClinicalData()]
        return permissions_list

    def get_queryset(self):
        clinic = getattr(self.request, "clinic", None)
        if clinic is None:
            return MedicalRecordShare.objects.none()
        return (
            MedicalRecordShare.objects.filter(clinic=clinic)
            .select_related("record__patient", "created_by")
            .order_by("-created_at")
        )

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        share = serializer.save()
        return Response(
            self.get_serializer(share).data,
            status=http_status.HTTP_201_CREATED,
        )

    @extend_schema(
        request=None,
        responses=MedicalRecordShareSerializer,
        summary="Révoquer un partage",
    )
    @action(detail=True, methods=["post"])
    def revoke(self, request, pk=None):
        share = self.get_object()
        share.revoke(user=request.user)
        return Response(self.get_serializer(share).data)
