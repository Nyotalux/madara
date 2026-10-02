"""Points de terminaison API des dossiers patients (namespace ``api``)."""

from __future__ import annotations

from django.db.models import Q
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.common.clinic import ClinicContextMixin
from apps.common.permissions import (
    CanReadClinicalData,
    CanWriteClinicalData,
    IsActiveMember,
    IsClinicAdmin,
)

from .models import Patient
from .serializers import (
    PatientArchiveSerializer,
    PatientListSerializer,
    PatientSerializer,
)


@extend_schema_view(
    list=extend_schema(
        summary="Lister les patients de la clinique courante",
        parameters=[
            OpenApiParameter(
                "q",
                str,
                description="Recherche sur nom, téléphone, CIN, référence.",
            ),
            OpenApiParameter("gender", str, description="F, M ou O."),
            OpenApiParameter(
                "include_archived", bool, description="Inclure les archivés."
            ),
        ],
    ),
    retrieve=extend_schema(summary="Consulter un dossier patient"),
    create=extend_schema(summary="Créer un dossier patient"),
    update=extend_schema(summary="Remplacer un dossier patient"),
    partial_update=extend_schema(summary="Modifier un dossier patient"),
    destroy=extend_schema(summary="Suppression refusée : archiver le dossier"),
)
class PatientViewSet(ClinicContextMixin, viewsets.ModelViewSet):
    """Dossiers patients, cloisonnés par la clinique courante.

    Lecture : accueil, médecin, infirmier, administrateur.
    Écriture : même périmètre (la comptabilité est exclue).
    Archivage : administrateur de clinique.
    """

    permission_classes = [IsActiveMember, CanReadClinicalData]
    filterset_fields = ["gender", "is_insured", "city"]
    search_fields = ["reference", "first_name", "last_name", "phone", "cin", "email"]
    # DELETE n'est jamais autorisé : l'historique médical doit être conservé.
    http_method_names = ["get", "post", "put", "patch", "head", "options"]

    def get_serializer_class(self):
        if self.action in {"list", "search"}:
            return PatientListSerializer
        return PatientSerializer

    def get_permissions(self):
        permissions_list = super().get_permissions()
        if self.action in {"create", "update", "partial_update"}:
            return [*permissions_list, CanWriteClinicalData()]
        if self.action in {"archive", "restore"}:
            return [*permissions_list, IsClinicAdmin()]
        return permissions_list

    def get_queryset(self):
        clinic = getattr(self.request, "clinic", None)
        if clinic is None:
            return Patient.objects.none()

        params = self.request.query_params
        include_archived = params.get("include_archived") in ("1", "true", "True")

        # Le manager filtre sur la clinique courante ; on l'explicite pour rester
        # sûr même si le contexte n'est jamais installé. ``all_objects`` est
        # nécessaire pour sortir les archives du filtre ``is_active``.
        manager = Patient.all_objects if include_archived else Patient.objects
        queryset = manager.filter(clinic=clinic).select_related("created_by")

        needle = (params.get("q") or params.get("search") or "").strip()
        if needle:
            queryset = queryset.filter(
                Q(reference__icontains=needle)
                | Q(first_name__icontains=needle)
                | Q(last_name__icontains=needle)
                | Q(phone__icontains=needle)
                | Q(cin__icontains=needle)
                | Q(email__icontains=needle)
            )
        return queryset.order_by("last_name", "first_name")

    def perform_create(self, serializer):
        serializer.save(clinic=getattr(self.request, "clinic", None))

    @extend_schema(
        request=PatientArchiveSerializer,
        responses=PatientSerializer,
        summary="Archiver un dossier (administrateur)",
    )
    @action(detail=True, methods=["post"])
    def archive(self, request, pk=None):
        patient = self.get_object()
        serializer = PatientArchiveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        patient.archive(serializer.validated_data.get("reason", ""))
        return Response(PatientSerializer(patient).data, status=status.HTTP_200_OK)

    @extend_schema(responses=PatientSerializer, summary="Restaurer un dossier archivé")
    @action(detail=True, methods=["post"])
    def restore(self, request, pk=None):
        patient = self.get_object()
        patient.restore()
        return Response(PatientSerializer(patient).data, status=status.HTTP_200_OK)

    @extend_schema(
        responses=PatientListSerializer(many=True),
        summary="Recherche rapide d'un patient",
    )
    @action(detail=False, methods=["get"])
    def search(self, request):
        """``GET /api/v1/patients/search/?q=salma`` — 10 résultats maximum."""
        needle = (request.query_params.get("q") or "").strip()
        if len(needle) < 2:
            return Response([])
        patients = self.filter_queryset(self.get_queryset()).filter(
            Q(first_name__icontains=needle)
            | Q(last_name__icontains=needle)
            | Q(phone__icontains=needle)
            | Q(reference__icontains=needle)
        )[:10]
        return Response(PatientListSerializer(patients, many=True).data)

    @extend_schema(responses={200: None}, summary="Compteurs de dossiers")
    @action(detail=False, methods=["get"])
    def statistics(self, request):
        queryset = self.get_queryset()
        return Response(
            {
                "visible": queryset.count(),
                "insured": queryset.filter(is_insured=True).count(),
            }
        )
