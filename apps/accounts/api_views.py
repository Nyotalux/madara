"""Points de terminaison API (namespace ``api``, préfixe ``/api/v1/``)."""

from __future__ import annotations

from django.utils.translation import gettext as _
from drf_spectacular.utils import extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.api import ok
from apps.common.middleware import CLINIC_SESSION_KEY
from apps.common.permissions import HasRole, IsActiveMember

from .models import BlockedSlot, Clinic, Membership, OpeningHour, Specialty
from .serializers import (
    BlockedSlotSerializer,
    ClinicSerializer,
    MembershipSerializer,
    MeSerializer,
    OpeningHourSerializer,
    SpecialtySerializer,
)


class MeView(APIView):
    """Profil de l'utilisateur connecté + clinique courante."""

    permission_classes = [IsAuthenticated]

    @extend_schema(responses=MeSerializer)
    def get(self, request):
        serializer = MeSerializer(
            request.user, context={"clinic": getattr(request, "clinic", None)}
        )
        return Response(serializer.data)

    @extend_schema(request=MeSerializer, responses=MeSerializer)
    def patch(self, request):
        serializer = MeSerializer(
            request.user,
            data=request.data,
            partial=True,
            context={"clinic": getattr(request, "clinic", None)},
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class SwitchClinicView(APIView):
    """Change la clinique courante (en-tête ``X-Clinic`` au prochain appel)."""

    permission_classes = [IsActiveMember]

    @extend_schema(
        request=ClinicSerializer,
        responses=ClinicSerializer,
        summary="Basculer de clinique",
    )
    def post(self, request):
        clinic_id = request.data.get("clinic")
        clinics = (
            Clinic.objects.all()
            if request.user.is_platform_staff
            else request.user.clinics
        )
        clinic = clinics.filter(pk=clinic_id).first()
        if clinic is None:
            return Response(
                {"error": {"message": _("Clinique inaccessible.")}},
                status=status.HTTP_403_FORBIDDEN,
            )
        request.session[CLINIC_SESSION_KEY] = clinic.pk
        return ok(ClinicSerializer(clinic).data)


class ClinicViewSet(viewsets.ReadOnlyModelViewSet):
    """Cliniques accessibles à l'utilisateur connecté."""

    serializer_class = ClinicSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if user.is_platform_staff:
            return Clinic.objects.all()
        return user.clinics

    @extend_schema(summary="Clinique courante")
    @action(detail=False, methods=["get"])
    def current(self, request):
        clinic = getattr(request, "clinic", None)
        if clinic is None:
            return Response({"detail": _("Aucune clinique sélectionnée.")}, status=404)
        return Response(self.get_serializer(clinic).data)


class MembershipViewSet(viewsets.ReadOnlyModelViewSet):
    """Rôles de l'utilisateur dans chaque clinique."""

    serializer_class = MembershipSerializer
    permission_classes = [IsActiveMember]

    def get_queryset(self):
        return (
            Membership.objects.filter(user=self.request.user)
            .select_related("clinic", "user", "practitioner_profile")
            .order_by("clinic__name")
        )


class StaffViewSet(viewsets.ReadOnlyModelViewSet):
    """Personnel de la clinique courante (mobile : annuaire du cabinet)."""

    serializer_class = MembershipSerializer
    permission_classes = [IsActiveMember]
    filterset_fields = ["role", "is_active"]

    def get_queryset(self):
        clinic = getattr(self.request, "clinic", None)
        queryset = Membership.objects.select_related("user", "practitioner_profile")
        return queryset.filter(clinic=clinic) if clinic else queryset.none()


class SpecialtyViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = SpecialtySerializer
    permission_classes = [IsActiveMember]
    pagination_class = None

    def get_queryset(self):
        return Specialty.objects.all()


class OpeningHourViewSet(viewsets.ModelViewSet):
    serializer_class = OpeningHourSerializer
    permission_classes = [IsActiveMember, HasRole.with_roles("DOCTOR", "NURSE", "ADMIN")]

    def get_queryset(self):
        clinic = getattr(self.request, "clinic", None)
        queryset = OpeningHour.objects.select_related("membership__user")
        return queryset.filter(membership__clinic=clinic) if clinic else queryset.none()

    def perform_create(self, serializer):
        serializer.save()

    @extend_schema(summary="Horaires du praticien connecté")
    def list(self, request, *args, **kwargs):
        membership = getattr(request, "clinic_membership", None)
        if membership is None:
            return super().list(request, *args, **kwargs)
        hours = self.get_queryset().filter(membership=membership)
        return Response(OpeningHourSerializer(hours, many=True).data)


class BlockedSlotViewSet(viewsets.ModelViewSet):
    serializer_class = BlockedSlotSerializer
    permission_classes = [IsActiveMember, HasRole.with_roles("DOCTOR", "NURSE", "ADMIN")]
    filterset_fields = ["membership", "start_at", "end_at"]

    def get_queryset(self):
        clinic = getattr(self.request, "clinic", None)
        queryset = BlockedSlot.objects.select_related("membership__user")
        return queryset.filter(membership__clinic=clinic) if clinic else queryset.none()
