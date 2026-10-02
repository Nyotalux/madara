"""Points de terminaison API de l'agenda (namespace ``api``)."""

from __future__ import annotations

from datetime import datetime, timedelta

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from apps.common.clinic import ClinicContextMixin
from apps.common.permissions import (
    CanReadClinicalData,
    CanWriteClinicalData,
    IsActiveMember,
)
from apps.patients.models import Patient

from . import services
from .forms import practitioners_of
from .models import Appointment
from .serializers import (
    AppointmentPatientAutocompleteSerializer,
    AppointmentSerializer,
    AppointmentStatusSerializer,
    SlotSerializer,
)


@extend_schema_view(
    list=extend_schema(
        summary="Lister les rendez-vous",
        parameters=[
            OpenApiParameter("date", str, description="Jour (AAAA-MM-JJ)."),
            OpenApiParameter("du", str, description="Début de période (AAAA-MM-JJ)."),
            OpenApiParameter("au", str, description="Fin de période (AAAA-MM-JJ)."),
            OpenApiParameter(
                "practitioner", int, description="Identifiant du praticien."
            ),
            OpenApiParameter("status", str, description="Filtrer par statut."),
        ],
    ),
    retrieve=extend_schema(summary="Consulter un rendez-vous"),
    create=extend_schema(summary="Prendre un rendez-vous"),
    update=extend_schema(summary="Modifier un rendez-vous"),
    partial_update=extend_schema(summary="Modifier un rendez-vous (PATCH)"),
)
class AppointmentViewSet(ClinicContextMixin, ModelViewSet):
    """Rendez-vous, cloisonnés par la clinique courante.

    Lecture : accueil, médecin, infirmier, administrateur.
    Un médecin ou un infirmier ne voit que ses propres rendez-vous.
    """

    serializer_class = AppointmentSerializer
    permission_classes = [IsActiveMember, CanReadClinicalData]
    filterset_fields = ["status", "kind", "practitioner"]

    def get_permissions(self):
        permissions_list = super().get_permissions()
        if self.action in {"create", "update", "partial_update"}:
            return [*permissions_list, CanWriteClinicalData()]
        return permissions_list

    def get_serializer_class(self):
        if self.action == "patients":
            return AppointmentPatientAutocompleteSerializer
        return AppointmentSerializer

    def get_queryset(self):
        clinic = getattr(self.request, "clinic", None)
        if clinic is None:
            return Appointment.objects.none()

        queryset = Appointment.objects.filter(clinic=clinic).select_related(
            "patient", "practitioner", "practitioner__user"
        )
        membership = getattr(self.request, "clinic_membership", None)
        if (
            membership is not None
            and not self.request.user.is_platform_staff
            and membership.role in ("DOCTOR", "NURSE")
        ):
            queryset = queryset.filter(practitioner=membership)

        params = self.request.query_params
        tz = services.clinic_timezone(clinic)
        date_param = params.get("date")
        if date_param:
            day = _parse_date(date_param)
            start, end = services.day_bounds(day, tz)
            queryset = queryset.filter(start_at__lt=end, end_at__gt=start)
        else:
            start_day = _parse_date(params.get("du"), None)
            end_day = _parse_date(params.get("au"), None)
            if start_day or end_day:
                start = services.day_bounds(start_day, tz)[0] if start_day else None
                end = (
                    services.day_bounds(end_day + timedelta(days=1), tz)[0]
                    if end_day
                    else None
                )
                if start is not None:
                    queryset = queryset.filter(end_at__gt=start)
                if end is not None:
                    queryset = queryset.filter(start_at__lt=end)

        if params.get("include_cancelled") not in ("1", "true", "True"):
            queryset = queryset.exclude(status=Appointment.Status.CANCELLED)
        return queryset.order_by("start_at")

    def perform_create(self, serializer):
        data = serializer.validated_data
        clinic = getattr(self.request, "clinic", None)
        practitioner = data["practitioner"]
        patient = data["patient"]

        if patient.clinic_id != clinic.pk:
            raise DjangoValidationError(
                _("Le patient n'appartient pas à cette clinique.")
            )
        if practitioner.clinic_id != clinic.pk:
            raise DjangoValidationError(
                _("Le praticien n'exerce pas dans cette clinique.")
            )

        try:
            appointment = services.book_appointment(
                clinic=clinic,
                patient=patient,
                practitioner=practitioner,
                start_at=data["start_at"],
                end_at=data.get("end_at"),
                kind=data.get("kind", Appointment.Kind.CONSULTATION),
                status=data.get("status", Appointment.Status.PENDING),
                reason=data.get("reason", ""),
                notes=data.get("notes", ""),
                user=self.request.user,
            )
        except DjangoValidationError:
            # Traduit en 400 par ``madara_exception_handler``.
            raise

        serializer.instance = appointment

    def perform_update(self, serializer):
        appointment = serializer.instance
        data = serializer.validated_data
        appointment.patient = data.get("patient", appointment.patient)
        appointment.practitioner = data.get("practitioner", appointment.practitioner)
        appointment.kind = data.get("kind", appointment.kind)
        appointment.reason = data.get("reason", appointment.reason)
        appointment.notes = data.get("notes", appointment.notes)
        if "start_at" in data or "end_at" in data:
            services.reschedule(
                appointment,
                start_at=data.get("start_at", appointment.start_at),
                end_at=data.get("end_at", appointment.end_at),
            )
        else:
            appointment.save()

    @extend_schema(
        request=AppointmentStatusSerializer,
        responses=AppointmentSerializer,
        summary="Changer le statut (arrivé, en cours, terminé, annulé)",
    )
    @action(detail=True, methods=["post"])
    def status(self, request, pk=None):
        appointment = self.get_object()
        serializer = AppointmentStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            services.set_status(
                appointment,
                serializer.validated_data["status"],
                reason=serializer.validated_data.get("reason", ""),
                user=request.user,
            )
        except DjangoValidationError:
            raise
        return Response(AppointmentSerializer(appointment).data)

    @extend_schema(
        request=None,
        responses=AppointmentSerializer,
        summary="Annuler un rendez-vous",
    )
    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        appointment = self.get_object()
        try:
            appointment.cancel(request.data.get("reason", ""), user=request.user)
        except DjangoValidationError:
            raise
        return Response(AppointmentSerializer(appointment).data)

    @extend_schema(
        parameters=[
            OpenApiParameter("date", str, description="Jour (AAAA-MM-JJ)."),
            OpenApiParameter(
                "practitioner", int, description="Identifiant du praticien."
            ),
        ],
        responses=SlotSerializer(many=True),
        summary="Créneaux disponibles d'un praticien",
    )
    @action(detail=False, methods=["get"])
    def availability(self, request):
        clinic = getattr(request, "clinic", None)
        tz = services.clinic_timezone(clinic)
        day = _parse_date(request.query_params.get("date"), timezone.localtime(tz).date())

        practitioners = practitioners_of(clinic)
        practitioner_id = request.query_params.get("practitioner")
        if practitioner_id:
            practitioners = practitioners.filter(pk=practitioner_id)

        membership = getattr(request, "clinic_membership", None)
        if (
            membership is not None
            and not request.user.is_platform_staff
            and membership.role in ("DOCTOR", "NURSE")
        ):
            practitioners = practitioners.filter(pk=membership.pk)

        result = []
        for practitioner in practitioners:
            slots = services.available_slots(practitioner, day, clinic=clinic, tz=tz)
            result.append(
                {
                    "practitioner": practitioner.pk,
                    "practitioner_name": practitioner.user.full_name,
                    "day": day.isoformat(),
                    "slots": SlotSerializer(slots, many=True).data,
                }
            )
        return Response(
            {
                "day": day.isoformat(),
                "practitioners": result,
            }
        )

    @extend_schema(
        parameters=[OpenApiParameter("q", str, description="Nom, téléphone, référence.")],
        responses=AppointmentPatientAutocompleteSerializer(many=True),
        summary="Recherche patient pour prendre rendez-vous",
    )
    @action(detail=False, methods=["get"])
    def patients(self, request):
        clinic = getattr(request, "clinic", None)
        needle = (request.query_params.get("q") or "").strip()
        queryset = Patient.objects.filter(clinic=clinic)
        if len(needle) >= 2:
            queryset = queryset.filter(
                Q(first_name__icontains=needle)
                | Q(last_name__icontains=needle)
                | Q(phone__icontains=needle)
                | Q(reference__icontains=needle)
            )
        else:
            queryset = queryset.none()
        return Response(
            AppointmentPatientAutocompleteSerializer(queryset[:10], many=True).data
        )


def _parse_date(value: str | None, default=None):
    if not value:
        return default
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return default
