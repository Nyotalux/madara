"""Points de terminaison API des notifications (namespace ``api``).

Un utilisateur ne lit et n'agit jamais que sur ses propres notifications,
restreintes à la clinique courante (en-tête ``X-Clinic``).
"""

from __future__ import annotations

from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework.decorators import action
from rest_framework.mixins import ListModelMixin, RetrieveModelMixin
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import GenericViewSet

from apps.common.clinic import ClinicContextMixin
from apps.common.permissions import IsActiveMember

from .models import Notification, PushSubscription
from .providers import vapid_public_key
from .serializers import NotificationSerializer


@extend_schema_view(
    list=extend_schema(
        summary="Lister ses notifications",
        parameters=[
            OpenApiParameter("status", str, description="pending|sent|failed."),
            OpenApiParameter("channel", str, description="EMAIL|WHATSAPP|IN_APP."),
            OpenApiParameter("category", str, description="Catégorie métier."),
            OpenApiParameter("unread", bool, description="Uniquement les non lues (1)."),
        ],
    ),
    retrieve=extend_schema(summary="Consulter une notification"),
)
class NotificationViewSet(
    ClinicContextMixin, ListModelMixin, RetrieveModelMixin, GenericViewSet
):
    """Notifications de l'utilisateur connecté, cloisonnées par clinique."""

    serializer_class = NotificationSerializer
    permission_classes = [IsActiveMember]
    filterset_fields = ["channel", "category", "status"]

    def get_queryset(self):
        user = self.request.user
        clinic = getattr(self.request, "clinic", None)
        if clinic is None or not user.is_authenticated:
            return Notification.objects.none()
        queryset = Notification.objects.filter(
            clinic=clinic, recipient=user
        ).select_related("patient", "appointment")
        if self.request.query_params.get("unread") in ("1", "true", "True"):
            queryset = queryset.filter(read_at__isnull=True)
        return queryset

    @extend_schema(
        request=None,
        responses=NotificationSerializer,
        summary="Marquer une notification comme lue",
    )
    @action(detail=True, methods=["post"])
    def read(self, request, pk=None):
        notice = self.get_object()
        if not notice.mark_read(user=request.user):
            return Response(
                {"detail": _("Impossible de marquer cette notification.")}, status=403
            )
        return Response(NotificationSerializer(notice).data)

    @extend_schema(
        request=None,
        responses={
            "200": {"type": "object", "properties": {"updated": {"type": "integer"}}}
        },
        summary="Marquer toutes ses notifications comme lues",
    )
    @action(detail=False, methods=["post"])
    def read_all(self, request):
        updated = Notification.objects.filter(
            clinic=getattr(request, "clinic", None),
            recipient=request.user,
            read_at__isnull=True,
        ).update(read_at=timezone.now())
        return Response({"updated": updated})


class PushSubscriptionAPI(APIView):
    """Abonnement Web Push du navigateur (POST = inscription, DELETE = retrait).

    Le navigateur envoie la souscription produite par le service worker ;
    un même endpoint est mis à jour au lieu d'être dupliqué.
    """

    permission_classes = [IsActiveMember]

    @extend_schema(
        request={
            "type": "object",
            "required": ["endpoint", "keys"],
            "properties": {
                "endpoint": {"type": "string"},
                "keys": {
                    "type": "object",
                    "properties": {
                        "p256dh": {"type": "string"},
                        "auth": {"type": "string"},
                    },
                },
                "user_agent": {"type": "string"},
            },
        },
        responses={"200": {"type": "object", "properties": {"id": {"type": "string"}}}},
        summary="Enregistrer l'abonnement Web Push du navigateur",
    )
    def post(self, request):
        endpoint = (request.data.get("endpoint") or "").strip()
        keys = request.data.get("keys") or {}
        if not endpoint:
            return Response({"detail": _("Abonnement invalide.")}, status=400)
        subscription, _created = PushSubscription.objects.get_or_create(
            endpoint=endpoint[:500],
            defaults={
                "user": request.user,
                "p256dh": (keys.get("p256dh") or "")[:120],
                "auth": (keys.get("auth") or "")[:60],
                "user_agent": (request.data.get("user_agent") or "")[:255],
            },
        )
        # Le navigateur peut reformer sa souscription : clés mises à jour.
        subscription.user = request.user
        subscription.is_active = True
        if keys.get("p256dh"):
            subscription.p256dh = keys["p256dh"][:120]
        if keys.get("auth"):
            subscription.auth = keys["auth"][:60]
        if request.data.get("user_agent"):
            subscription.user_agent = request.data["user_agent"][:255]
        subscription.save()
        return Response({"id": str(subscription.uuid)})

    @extend_schema(
        request={
            "type": "object",
            "required": ["endpoint"],
            "properties": {"endpoint": {"type": "string"}},
        },
        responses={"200": {"type": "object", "properties": {"ok": {"type": "boolean"}}}},
        summary="Retirer l'abonnement Web Push d'un appareil",
    )
    def delete(self, request):
        endpoint = (request.data.get("endpoint") or "").strip()
        PushSubscription.objects.filter(
            user=request.user, endpoint=endpoint[:500]
        ).delete()
        return Response({"ok": True})


class WebPushStatusAPI(APIView):
    """Configuration Web Push exposée au navigateur (clé publique VAPID)."""

    permission_classes = [IsActiveMember]

    @extend_schema(summary="Clé publique VAPID pour s'abonner")
    def get(self, request):
        return Response({"public_key": vapid_public_key()})
