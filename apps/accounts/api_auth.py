"""Authentification par jeton pour les applications mobiles (JWT)."""

from __future__ import annotations

from django.utils.translation import gettext as _
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from apps.common.clinic import ClinicContextMixin

from .serializers import ClinicSerializer, MembershipSerializer


class MadaraTokenSerializer(TokenObtainPairSerializer):
    """Retourne les jetons + le profil + les cliniques de l'utilisateur."""

    @extend_schema(responses=None)
    def validate(self, attrs):
        data = super().validate(attrs)
        user = self.user

        data["user"] = {
            "id": user.id,
            "email": user.email,
            "full_name": user.full_name,
            "is_platform_staff": user.is_platform_staff,
        }
        data["memberships"] = MembershipSerializer(
            user.memberships.filter(is_active=True), many=True
        ).data
        data["clinics"] = ClinicSerializer(user.clinics, many=True).data
        data["default_clinic_id"] = (
            user.primary_membership.clinic_id if user.primary_membership else None
        )
        return data


class TokenObtainPairWithClinicView(TokenObtainPairView):
    """``POST /api/v1/auth/login`` -> jeton d'accès + contexte clinique."""

    serializer_class = MadaraTokenSerializer
    permission_classes = [AllowAny]


class RefreshTokenView(TokenRefreshView):
    permission_classes = [AllowAny]


class LogoutView(ClinicContextMixin, APIView):
    """Invalide la session applicative côté client (déconnexion)."""

    permission_classes = [AllowAny]

    @extend_schema(
        request=inline_serializer(
            "LogoutRequest", {"refresh": serializers.CharField(required=False)}
        ),
        responses={200: None},
        summary="Déconnexion",
        auth=[],
    )
    def post(self, request):
        refresh = request.data.get("refresh")
        if not refresh:
            return Response(
                {"error": {"message": _("Jeton de rafraîchissement manquant.")}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # Sans blacklist applicative, la révocation est assurance par expiration ;
        # le client doit supprimer ses jetons.
        return Response({"detail": _("Déconnexion effectuée.")})
