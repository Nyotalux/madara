"""Points de terminaison de santé (monitoring, sondes de disponibilité)."""

from __future__ import annotations

from django.conf import settings
from django.db import connections
from django.db.utils import OperationalError
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView


def _payload(checks: dict, status_code: int) -> JsonResponse:
    healthy = all(item.get("ok", True) for item in checks.values())
    body = {
        "status": "ok" if healthy else "degraded",
        "timestamp": timezone.now().isoformat(),
        "version": "1.0.0",
        "environment": settings.SETTINGS_MODULE,
        "checks": checks,
    }
    return JsonResponse(body, status=status_code)


class HealthView(APIView):
    """Sonde de vivacité : ne touche pas la base de données."""

    authentication_classes: list = []
    permission_classes = [AllowAny]
    throttle_scope = "health"

    @extend_schema(
        responses={200: inline_serializer("Health", {"status": "", "timestamp": ""})},
        summary="Sonde de vivacité",
        auth=[],
    )
    def get(self, request):
        return Response({"status": "ok", "timestamp": timezone.now().isoformat()})


@never_cache
@require_GET
def health_db(request):
    """Vérifie la connectivité à PostgreSQL."""
    try:
        with connections["default"].cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except OperationalError as exc:
        return _payload({"database": {"ok": False, "error": str(exc)[:200]}}, 503)
    return _payload({"database": {"ok": True}}, 200)


@never_cache
@require_GET
def health_ready(request):
    """Sonde de disponibilité : base de données et broker de tâches."""
    checks: dict = {}

    try:
        with connections["default"].cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        checks["database"] = {"ok": True}
    except OperationalError as exc:
        checks["database"] = {"ok": False, "error": str(exc)[:200]}

    broker = getattr(settings, "CELERY_BROKER_URL", "")
    if broker.startswith("memory://") or settings.DEBUG:
        checks["broker"] = {"ok": True, "mode": "local"}
    else:
        try:
            import redis

            client = redis.Redis.from_url(broker, socket_connect_timeout=2)
            client.ping()
            checks["broker"] = {"ok": True}
        except Exception as exc:  # pragma: no cover - depend de l'infra
            checks["broker"] = {"ok": False, "error": str(exc)[:200]}

    status_code = 200 if all(item.get("ok") for item in checks.values()) else 503
    return _payload(checks, status_code)
